# ACP 任务文件布局规范（Zone 契约）

**版本**: v1.2 · 2026-08-23
**状态**: ✅ 生效（v1.2：任务目录命名全面切换 v2 + 调度器任务扁平化 + job_id 内化，见 §1a/§2a/§6a）

---

## 1. 设计原则

ACP 的前端文件树采用**本地磁盘直接映射**（后端 `build_manifest` 逐层直读 + 前端 `buildTree` 原样重建）。这一设计被保留——磁盘即真相，断点续算、retention 清理、远程同步全部依赖真实路径。本规范的目标不是"重构目录树"，而是：

1. **冻结**调度器与工作流各自的目录归属（Zone A/B），防止漂移；
2. **新增一层产物摘要**（Zone C：`result_summary.json`），让文件树可以在原始浏览之上提供"结果直达"视图。

### 1a. 任务命名（v2，2026-08-23 起强制）

所有调度器任务的物理目录名统一为（`storage/layout.py::sanitize_task_dir_name`）：

```
<分子名>_<计算任务名>_<备注>     # 备注可省略；重名时追加 __02 起的短序号（文件系统去重）
```

- `job_id`（`YYYYMMDD_HHMMSS_NNN_…` 时间戳格式）**只作为数据库主键与日志内容存在，禁止出现在任何磁盘路径中**（§6a）。
- `task_name` 缺省时由 API 层默认取 workflow 名；`molecule_name` 缺省取输入文件 stem 或 `mol`。
- 新任务的用户可见任务名、`task.json.display_name`、任务索引 `display_name` 与物理目录叶子名必须相同，均取最终去重后的 `task_dir_name`；历史任务读取时优先显示其实际目录叶子名。
- 调度器 API 的 `output_dir` 只作为任务父目录覆盖，后端仍追加规范化后的 `task_dir_name`，不能用任意目录叶子替代任务名。
- 非调度器 CLI 直跑（`acp run energy --output ./out`，含 `--batch-file` 多分子）**不走本节**——多分子批跑仍需 `{output}/{safe_name}/` 每分子子目录。

## 2. 目录结构总览（v1.2 调度器任务）

```
<run_root>/<project_id>/<task_dir_name>/
├── job.json                  # Zone A: 调度器 job 快照（含 job_id —— job_id 的磁盘锚点之一）
├── task.json                 # Zone A: 任务快照（含 job_id + task_dir_name + workflow）
├── input.xyz                 # Zone A: 调度器物化的分子输入（SMILES 来源时另有 input_source.json）
├── state.json                # Zone A: WorkflowState checkpoint（v1.2 起位于任务根；core/state.py，resume 关键）
├── WORK/00_RUNTIME/          # Zone A: stdout.log / stderr.log / events.jsonl（日志头部含 job_id，§6a）
├── RESULT/                   # Zone A+B: 调度器预创建；新任务唯一的分类结果目录
├── metrics.json              # Zone A: QC 运行时指标旁车（MetricsExtractor，display-only）
├── mechanism_config.json     # Zone B: mechanism 独有（调度器写入，经 --mechanism-config 传入）
└── mechanism_study/<study_id>/  # 仅历史任务（v2 归一化后新任务不再创建；见 §4 legacy 兼容）
```

### 2a. 调度器任务扁平化（v1.2）

调度器语境（工作流侧探测契约：`job.json` 与 `task.json` 同时存在于输出根，见 `workflows/_helpers.resolve_task_output_root`）下，工作流产物**直接落于任务根**：`WORK/02_SEARCH/…`、`WORK/03_OPT/…`、`RESULT/…`、`state.json` 均在 `<task_dir_name>/` 下，**不再有 `{safe_name}/` 二级嵌套**。

历史任务（`{job_id}/{safe_name}/…` 嵌套布局）不迁移、保持可读：`find_workflow_state` 浅层优先 + rglob、`runtime_file` 双布局解析、DB `work_dir` 列为路径权威来源。

**v1 残留已移除**（2026-08-23）：小写脚手架目录 `inputs/ work/ results/` 停止创建；`_resolve_work_dir` 的 legacy job_id 分支、`JobSpec.task_dir_name` 的 legacy 回退、休眠的 set-based `dedup_task_dir_name` 已删除。

## 3. Zone 定义与规则

| Zone | 所有者 | 内容 | 规则 |
|---|---|---|---|
| **A** | 调度器 | `job.json` `task.json` `input.xyz` `state.json` `WORK/00_RUNTIME/{stdout,stderr,events}` `metrics.json` | **冻结**。命名与位置由 `runner.py` / `manager.py` 决定；任何工作流不得迁移、重命名、复用 |
| **B** | 工作流 | 各工作流自身产物目录（`WORK/02_SEARCH` 等阶段目录、`RESULT/` 类别目录） | 名称**契约化稳定**；不强制同构，但已有名称不得改名 |
| **B'** | 工作流（历史） | `mechanism_study/`（仅旧任务，双探针只读） | 见 §4 legacy 兼容 |
| **C** | 工作流 finalize | `result_summary.json`（Zone C 指针文件） | 约定见 §5 |

### Zone B 现状清单（冻结）

| 工作流 | 产物根（调度器任务） | 说明 |
|---|---|---|
| ensemble / energy / xtbmd_censo_energy | 任务根 `WORK/{02_SEARCH,03_OPT}` + `RESULT/` | `RESULT/{structures,energies,ensembles}/` 经 `energy_shared.write_final_outputs` 统一收口；历史 `finalDFT/` 仅只读兼容 |
| nmr | 任务根（本就平铺） | `nmr_report.json`、`nmr_assignment.xlsx`、plots；`nmr_summary.json` |
| simple (singlepoint/opt/freq/optfreq/optfreqsp/scan/xtb-opt) | 任务根 `WORK/<stage>` | `optimized.xyz`、`energy.json`、`frequencies.txt`、`thermo.json` |
| mechanism | `WORK/{02_SEARCH,03_OPT/TS,07_PATH,08_ANALYSIS}` + `RESULT/mechanism` | 见 §4（v2 归一化；旧任务读 legacy） |

非调度器 CLI 语境下，上表各"任务根"替换为 `{output}/{safe_name}/`（多分子批跑必需）。

## 4. mechanism 研究目录（Zone B · v2 归一化，2026-08-23）

v1.2 起机理任务与普通任务遵守**同一目录契约**（v2 设计 §6.4/§13）——`mechanism_study/<study_id>/` 第三套命名体系已废除，`study_id` 仅存于 DB 主键与 checkpoint 指纹，不再出现在任何磁盘路径中。全部产物按阶段落入统一 `WORK/<stage>/` 树：

```
WORK/
├── 01_PREPARE/inputs/              # 稳定态输入几何（state_*.xyz）
├── 02_SEARCH/{s1,s1_xtbfast,states}/  # S1 构象系综 + states 清单
├── 03_OPT/{TS,refinements}/        # S3/S4 TS 精修（s3s4→TS）+ 精修清单
├── 07_PATH/{s2,s2_peb,sr,routes}/  # S2 路径搜索 + 端点 + 路径清单
└── 08_ANALYSIS/                    # 检查点根：study.json network.json reaction.json
    ├── quality_gates.json events.jsonl
    ├── cycles/cycle_NN/            # SR 审核周期（revision.json）
    └── decisions/<id>.json
RESULT/mechanism/                   # v2 投影：reaction_network/route_summary/ts_summary/
                                    # irc_validation/energy_profile.json + structures/
```

**路径解析**统一经 `acp/mechanism/layout.py`：`resolve_study_layout(task_root, study_id)`（新布局，写路径用）、`find_study_layout(task_root)`（**双探针**：新布局 study.json → legacy `mechanism_study/*/study.json` 回退，读路径用）、`find_reaction_json`（预运行物化探测）。`study_dir` 别名 = `WORK/08_ANALYSIS`（checkpoint 根，`study.study_dir` 持久化字段）。

**历史兼容**：旧 `mechanism_study/<study_id>/` 任务**不迁移**，经 `LEGACY_FALLBACK_ENABLED`（layout.py）保持可读可续算——resume、result 投影、reports、remote 上传全部走双探针。新任务零 legacy 痕迹。

## 5. Zone C：result_summary.json（新增约定）

### 5.1 位置

每个工作流在自身产物根（Zone B 的 `output_root` 子目录）写 `result_summary.json`。

### 5.2 Schema

```json
{
  "version": 1,
  "workflow": "energy",
  "products": [
    {"label": "Ranked conformers (XYZ)", "path": "RESULT/structures/all_conformers.xyz", "kind": "xyz"},
    {"label": "Ensemble thermo (G_total)", "path": "RESULT/energies/ensemble_thermo.json", "kind": "report"}
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

1. **`_SCHEDULER_MARKERS`**（`simple.py`）：调度器在 subprocess 启动前创建的任何文件（如 `metrics.json`）必须加入该集合，否则 `_resolve_output_dir` 会把 simple 工作流重定向到 `<work_dir>_1/` 兄弟目录。v1.2 起集合为：`submit.lsf` `.exit_code` `events.jsonl` `job.json` `stdout.log` `stderr.log` `mechanism_config.json` `metrics.json` `WORK` `RESULT` `input.xyz` `task.json` `input_source.json`（小写 `inputs/work/results` 已随脚手架移除而删除）。
2. **resume 兼容**：`result_summary.json` 与 `metrics.json` 均为 **write-only by 工作流/调度器，绝不参与 resume/checkpoint 判定**。`state.json`、`.stage_*`、`WORK/08_ANALYSIS/**`（mechanism checkpoint，双探针兼容 legacy `mechanism_study/**`）才是 checkpoint 真相源。
3. **display-only**：`metrics.json` 永不 gate 任何控制流（resume/purge/cleanup 不得依赖它）。
4. **工作流侧调度器探测契约**：`workflows/_helpers.is_scheduler_task_dir`（`job.json` + `task.json` 双文件存在）。调度器将来新增预创建文件时若影响该判定，必须同步本契约。
5. **旧布局只读兼容**：`{job_id}/{safe_name}/…` 历史任务不得迁移；所有读取路径（detail、文件树、purge、远程 state 观察）必须同时容忍两种布局（`find_workflow_state` 浅层优先 + rglob 模式是范例）。

## 6a. job_id 内化（v1.2 强制）

`job_id` 不再出现在任何磁盘路径/文件名中，其定位锚点为：

| 锚点 | 位置 | 内容 |
|---|---|---|
| 数据库 | `jobs.id`（主键）+ `jobs.work_dir`（路径权威来源） | job_id ↔ 任务目录映射 |
| `job.json` | 任务根 | 完整 JobRecord 快照（含 id） |
| `task.json` | 任务根 | job_id + task_dir_name + workflow |
| `WORK/00_RUNTIME/stdout.log` / `stderr.log` 头部 | 任务内日志 | runner 启动时写入的头部块：job id、workflow、task dir、work_dir、ISO 时间戳、完整命令行 |
| `WORK/00_RUNTIME/events.jsonl` | 任务内日志 | 每条事件自带 job_id |

排障入口统一为：DB/前端定位 job → `work_dir` → 同目录 `input.xyz` 旁的 `WORK/00_RUNTIME/` 读日志（日志头部即含 job_id，可反向 grep 定位）。

## 6b. `finalDFT/` 历史兼容边界

从当前版本起，`ensemble` / `energy` / `xtbmd_censo_energy` 的稳定结果统一写入 `RESULT/structures`、`RESULT/energies` 和 `RESULT/ensembles`。`result_summary.json`、`result_manifest.json` 以及前端结果直达区也只引用 `RESULT/` 路径；新任务不再由 `energy_shared.write_final_outputs()` 创建 `finalDFT/`。

`finalDFT/` 仅保留两类历史兼容用途：

- 旧版 `cccp.core.engine` 生成的 ORCA / Shermo 原始计算记录；
- 历史任务中的 `all_conformers.xyz`、`conformer_thermo.csv`、`ensemble_thermo.json` 等结果。

调度器读取 ensemble 热力学结果时优先读取 `RESULT/energies/ensemble_thermo.json`，找不到时才回退到历史 `finalDFT/ensemble_thermo.json`。结构来源识别也保留对旧 `finalDFT/all_conformers.xyz` 的只读支持。

清理原则：

1. 新任务不应再出现 `finalDFT/`；若出现，应视为旧流程或外部旧引擎产物，先核对任务时间和来源。
2. 历史任务只有在终态、确认 `RESULT/` 或结果摘要中的稳定产物可用，并完成必要归档后，才可删除 `finalDFT/`。
3. 运行中、暂停、等待审核或仍可续算的任务不得清理；失败任务应先保留原始日志用于排障。
4. 自动清理必须具备 dry-run、终态检查、白名单、归档校验和事件审计；本次代码变更不删除已有历史目录。
