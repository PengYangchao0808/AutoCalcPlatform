# ACP 任务文件布局规范（Zone 契约）

**版本**: v1 · 2026-08-18
**状态**: ✅ 生效（随 2026-08-18 Workbench 四问题修复引入）

---

## 1. 设计原则

ACP 的前端文件树采用**本地磁盘直接映射**（后端 `build_manifest` 逐层直读 + 前端 `buildTree` 原样重建）。这一设计被保留——磁盘即真相，断点续算、retention 清理、远程同步全部依赖真实路径。本规范的目标不是"重构目录树"，而是：

1. **冻结**调度器与工作流各自的目录归属（Zone A/B），防止漂移；
2. **新增一层产物摘要**（Zone C：`result_summary.json`），让文件树可以在原始浏览之上提供"结果直达"视图。

## 2. 目录结构总览

```
<run_root>/<project_id>/<job_id>/
├── job.json                  # Zone A: 调度器 job 快照（JobManager._write_job_json）
├── state.json                # Zone A: WorkflowState checkpoint（core/state.py，resume 关键）
├── events.jsonl              # Zone A: 溯源事件日志（JobEventLog）
├── stdout.log  stderr.log    # Zone A: 子进程标准输出/错误（真实日志，经 /jobs/{id}/logs 提供）
├── metrics.json              # Zone A: QC 运行时指标旁车（MetricsExtractor，display-only）
├── inputs/  work/  results/  # Zone A: runner 预创建的脚手架目录
├── mechanism_config.json     # Zone B: mechanism 独有（调度器写入，经 --mechanism-config 传入）
└── mechanism_study/<study_id>/  # Zone B: mechanism 独有，CHECKPOINT-FROZEN（见 §4）
    ├── reaction.json
    ├── calc/{s1,s1_xtbfast,s2,s2_peb,s3s4}/
    ├── cycles/cycle_NN/
    ├── routes/<source_id>__<route_id>/
    └── refinements/<manifest_id>/
```

## 3. Zone 定义与规则

| Zone | 所有者 | 内容 | 规则 |
|---|---|---|---|
| **A** | 调度器 | `job.json` `state.json` `events.jsonl` `stdout.log` `stderr.log` `metrics.json` `inputs/` `work/` `results/` | **冻结**。命名与位置由 `runner.py` / `manager.py` 决定；任何工作流不得迁移、重命名、复用 |
| **B** | 工作流 | 各工作流自身产物目录（如 `mechanism_study/`、工作流写入的 `output_root` 子目录） | 名称**契约化稳定**；不强制同构，但已有名称不得改名 |
| **C** | 工作流 finalize | `result_summary.json`（Zone C 指针文件） | **新增约定**，见 §5 |

### Zone B 现状清单（冻结）

| 工作流 | 产物根 | 说明 |
|---|---|---|
| ensemble / energy / xtbmd_censo_energy | `<work_dir>/<safe_name>/` | `finalDFT/`、`ensemble/`、`<name>_global_min.xyz`；经 `energy_shared.write_final_outputs` 统一收口 |
| nmr | `<work_dir>/<safe_name>/` | `nmr_report.json`、`nmr_assignment.xlsx`、plots；`nmr_summary.json` |
| simple (singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt) | `<work_dir>/<safe_name>/` | `optimized.xyz`、`energy.json`、`frequencies.txt`、`thermo.json` |
| mechanism | `<work_dir>/mechanism_study/<study_id>/` | 见 §4 |

## 4. mechanism 研究目录（Zone B · 冻结区，最高优先级约束）

```
mechanism_study/<study_id>/
├── reaction.json          # 锁定反应定义（reaction_definition.py 持久化）
├── calc/                  # 全部 QC 产物（study_runner.build_study_providers work_root）
│   ├── s1/                #   S1 构象系综（native-censo-lite）
│   ├── s1_xtbfast/        #   S0/S1 xtb-fast
│   ├── s2/                #   S2 路径搜索（guided-scan）
│   ├── s2_peb/            #   S2 PEB（rph-reverse）
│   └── s3s4/              #   S3/S4 精修
├── cycles/cycle_NN/       # SR 审核周期持久化（revision.json）
├── routes/<src>__<route>/ # 单路径扫描数据
└── refinements/<manifest_id>/  # TS 精修清单
```

**硬性规则**：此子树被 `orchestrator.py`、`study_runner.py`、`jobs.py::write_mechanism_reaction_json` 以及所有 resume 逻辑硬编码读取。**任何实现不得改名、移动、或附加显示语义**（如把 `mechanism_study` 改名为 `study`）。`study_id = study_YYYYMMDD_HHMMSS_<8-hex-sha256>`（`study_runner._default_study_id`），其哈希目录名是契约的一部分。

## 5. Zone C：result_summary.json（新增约定）

### 5.1 位置

每个工作流在自身产物根（Zone B 的 `output_root` 子目录）写 `result_summary.json`。

### 5.2 Schema

```json
{
  "version": 1,
  "workflow": "energy",
  "products": [
    {"label": "Ranked conformers (XYZ)", "path": "finalDFT/all_conformers.xyz", "kind": "xyz"},
    {"label": "Ensemble thermo (G_total)", "path": "finalDFT/ensemble_thermo.json", "kind": "report"}
  ]
}
```

- `path` 相对 `result_summary.json` 所在目录
- `kind` ∈ {`xyz`, `report`, `table`, `plot`, `file`}（自由扩展，前端按 kind 显示图标）
- 仅**指针**文件：不复制、不移动任何产物

### 5.3 写入点（统一收口）

| 工作流 | 写入函数 | 位置 |
|---|---|---|
| ensemble/energy/xtbmd | `write_final_outputs` | `energy_shared.py`（一处覆盖三工作流） |
| nmr | `run_nmr_analysis` 报告段 | `nmr.py` |
| simple 全系列 | 各入口成功路径 | `simple.py`（6 处） |
| mechanism | 待接入（P2）：orchestrator study-complete 钩子 | `orchestrator.py` |

通用写入器：`acp.workflows._helpers.write_result_summary(product_root, workflow, products)`。

### 5.4 消费方

- `build_manifest(work_dir, view="summary")`（`files.py`）递归发现 `result_summary.json`，将 products 重定位为相对 `work_dir` 的 `pinned` 数组（仅返回存在的文件，破损指针静默丢弃）
- `GET /api/v1/jobs/{id}/files?view=summary` 透传；前端在文件树顶部渲染"📌 结果产物"直达区
- **默认 view=raw**：行为与从前完全一致（零回归）

## 6. 相关防回归约束

1. **`_SCHEDULER_MARKERS`**（`simple.py`）：调度器在 subprocess 启动前创建的任何文件（如 `metrics.json`）必须加入该集合，否则 `_resolve_output_dir` 会把 simple 工作流重定向到 `<work_dir>_1/` 兄弟目录。
2. **resume 兼容**：`result_summary.json` 与 `metrics.json` 均为 **write-only by 工作流/调度器，绝不参与 resume/checkpoint 判定**。`state.json`、`.stage_*`、`mechanism_study/**` 才是 checkpoint 真相源。
3. **display-only**：`metrics.json` 永不 gate 任何控制流（resume/purge/cleanup 不得依赖它）。
