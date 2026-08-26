# ACP 项目—任务存储与结果目录设计 v2

**状态**：已实施（2026-08-23 完成 P1–P8 主体；3D 查看器 UI 与节点 Agent 存储为后续增强）  
**版本**：v2  
**日期**：2026-08-22  
**适用范围**：ACP Workbench、任务调度器、计算节点存储、工作流结果展示

> 本文档记录 ACP 后续目录和存储架构的目标方案。当前已生效的旧目录契约仍由
> `docs/ACP_Job_File_Layout_Spec.md` 维护；在 v2 完成迁移前，旧任务必须继续兼容。

---

## 1. 总体目标

ACP 的任务目录必须服务于“项目—任务”两层模型，而不是服务于某个具体工作流的临时输出结构。

核心原则：

1. 服务端管理项目、任务、状态、节点和路径索引。
2. 计算节点保存任务的完整文件，服务端不保存任务文件副本。
3. 一个普通任务只对应一个分子和一个 `input.xyz`。
4. 所有任务统一使用 `WORK/` 和 `RESULT/` 两个核心目录。
5. `WORK/` 保存真实计算过程，`RESULT/` 保存最终结果和可视化数据。
6. 所有任务统一使用“分子名—任务名—备注”的命名格式。
7. 机理类型、路线和不同机理方案通过分子名、任务名和备注区分，不引入第三套命名规则。

---

## 2. 项目与任务模型

### 2.1 项目

项目是用户组织多个计算任务的容器。

```text
Project
├── Task 1
├── Task 2
├── Task 3
└── ...
```

项目由服务端数据库管理，至少包含：

```text
projects
├── project_id
├── project_name
├── description
├── tags
├── created_at
└── updated_at
```

### 2.2 任务

任务是 ACP 的最小计算和文件管理单位。

```text
一个普通任务 = 一个分子 + 一个 input.xyz + 一套计算流程
```

一个项目可以同时提交多个任务，但不能把多个独立分子放入一个普通任务：

```text
正确：
Project/
├── ethanol_opt_final/
└── methanol_opt_final/

不采用：
Project/
└── opt_batch/
    ├── ethanol.xyz
    └── methanol.xyz
```

构象搜索产生的多个构象属于同一个分子的计算结果，不视为多个任务。

---

## 3. 计算节点目录结构

任务文件只存储在计算节点本地。

```text
<node_task_root>/
└── <project_name>/
    ├── <molecule>_<task>_<remark>/
    │   ├── input.xyz
    │   ├── task.json
    │   ├── WORK/
    │   └── RESULT/
    │
    ├── <molecule>_<task>_<remark>/
    │   ├── input.xyz
    │   ├── task.json
    │   ├── WORK/
    │   └── RESULT/
    │
    └── ...
```

示例：

```text
ACP_Calculations/
└── Concented_TS_Project/
    ├── XXXTS1_mechanism_route01/
    │   ├── input.xyz
    │   ├── task.json
    │   ├── WORK/
    │   └── RESULT/
    │
    ├── XXXTS2_mechanism_route01/
    │   ├── input.xyz
    │   ├── task.json
    │   ├── WORK/
    │   └── RESULT/
    │
    └── ethanol_opt_r2scan_final/
        ├── input.xyz
        ├── task.json
        ├── WORK/
        └── RESULT/
```

不再使用以下顶层脚手架目录：

```text
Inputs/
Results/
Work/
```

---

## 4. 任务命名规范

所有任务统一采用：

```text
<分子名>_<计算任务名>_<备注信息>
```

备注信息可以省略。

### 4.1 普通任务示例

```text
ethanol_conformer_initial
ethanol_opt_r2scan_final
ethanol_freq_validation
ethanol_optfreqsp_thermo
```

### 4.2 机理任务示例

```text
XXXTS1_mechanism_route01
XXXTS2_mechanism_route01
XXXTS1_mechanism_route02
XXXTS1_ts_optimization
XXXTS1_irc_validation
```

其中：

- `XXXTS1`：用户定义的分子名或机理标识。
- `mechanism`、`ts_optimization`：计算任务名。
- `route01`、`final`、`validation`：备注信息。

不同机理不使用额外的 `Study A`、`Mechanism B`、`study_id` 等用户可见命名层。

### 4.3 命名约束

- 空格统一转换为 `_`。
- 禁止 `/`、`\\`、`:`、`*`、`?`、`"`、`<`、`>`、`|`。
- 任务名必须包含分子名和计算任务名。
- 备注可选。
- 任务创建后，物理目录名不可自动改变。
- 服务端使用独立的 `task_id` 保证唯一性，不依赖目录名。
- 新任务的 `display_name` 与物理目录叶子名是同一个规范名称；若发生重复，最终名称统一追加短序号，例如 `ethanol_opt__02`。
- 重跑在原任务记录和原物理目录内递增 attempt，不创建新的 `task_id`、`job_id` 或任务目录；上一轮 WORK/RESULT 会归档到该任务的 `_attempts/attempt_NNN/` 下。
- 续跑在原任务记录和原物理目录内复用 checkpoint；暂停任务恢复时继续复用原有进程或远端 LSF 作业。
- 复制才创建新任务，继续使用独立任务 ID；复制不把 `_copy`、`__rerun` 等后缀写入任务显示名。

---

## 5. 任务内部固定结构

每个任务固定包含：

```text
任务目录/
├── input.xyz
├── task.json
├── WORK/
└── RESULT/
```

### 5.1 输入文件

`input.xyz` 直接放在任务根目录，不新增独立输入窗口或输入目录。

如果输入来自 SMILES，节点侧生成：

```text
任务目录/input.xyz
任务目录/input_source.json
```

服务端只保存输入摘要：

```text
input_hash
molecule_name
atom_count
charge
multiplicity
source_type
```

完整 XYZ 内容只存在于计算节点。

---

## 6. WORK 工作区

`WORK/` 是真实计算事实、断点恢复和审计的依据。

所有 ORCA、xTB、CREST、CENSO、ISOSTAT、Shermo 等程序的实际输入、输出和中间文件都保存在这里。

统一按计算阶段分类：

```text
WORK/
├── 00_RUNTIME/
│   ├── stdout.log
│   ├── stderr.log
│   ├── events.jsonl
│   └── runtime.json
│
├── 01_PREPARE/
│   ├── input_normalized.xyz
│   └── preparation.json
│
├── 02_SEARCH/
│   ├── CREST/
│   ├── xTB/
│   ├── CENSO/
│   └── ISOSTAT/
│
├── 03_OPT/
│   ├── ORCA/
│   ├── xTB/
│   └── TS/
│
├── 04_FREQ/
│   └── ORCA/
│
├── 05_SP/
│   └── ORCA/
│
├── 06_THERMO/
│   └── Shermo/
│
├── 07_PATH/
│   ├── route01/
│   ├── route02/
│   ├── scan/
│   ├── PEB/
│   └── IRC/
│
└── 08_ANALYSIS/
    ├── parsers/
    └── intermediate/
```

只创建任务实际使用的阶段目录，不创建无内容的额外目录。

### 6.1 单点任务

```text
WORK/
├── 00_RUNTIME/
└── 05_SP/
    └── ORCA/
        ├── calculation.inp
        ├── calculation.out
        └── ...
```

### 6.2 优化和频率任务

```text
WORK/
├── 00_RUNTIME/
├── 03_OPT/
│   └── ORCA/
├── 04_FREQ/
│   └── ORCA/
└── 08_ANALYSIS/
```

### 6.3 构象搜索任务

```text
WORK/
├── 00_RUNTIME/
├── 02_SEARCH/
│   ├── CREST/
│   ├── xTB/
│   ├── CENSO/
│   └── ISOSTAT/
└── 08_ANALYSIS/
```

### 6.4 机理任务

```text
WORK/
├── 00_RUNTIME/
├── 01_PREPARE/
├── 02_SEARCH/
├── 03_OPT/
│   └── TS/
├── 07_PATH/
│   ├── route01/
│   ├── route02/
│   └── IRC/
└── 08_ANALYSIS/
```

---

## 7. RESULT 结果区

`RESULT/` 只保存最终结果、统计数据和可视化数据，不保存完整的原始计算过程。

统一结构：

```text
RESULT/
├── result_manifest.json
├── summary.json
├── structures/
├── energies/
├── frequencies/
├── trajectories/
├── ensembles/
├── mechanism/
└── reports/
```

实际任务只创建有结果的类别。

### 7.1 优化任务

```text
RESULT/
├── result_manifest.json
├── summary.json
├── structures/
│   ├── input.xyz
│   └── optimized.xyz
├── trajectories/
│   └── optimization.xyz
└── energies/
    └── energy_summary.json
```

### 7.2 频率任务

```text
RESULT/
├── result_manifest.json
├── summary.json
└── frequencies/
    ├── frequencies.json
    ├── normal_modes.json
    ├── imaginary_modes.json
    └── ir_intensities.json
```

### 7.3 构象搜索任务

```text
RESULT/
├── result_manifest.json
├── summary.json
├── ensembles/
│   ├── conformers.xyz
│   ├── conformer_energies.csv
│   ├── boltzmann_distribution.csv
│   └── ensemble_summary.json
└── structures/
    └── global_minimum.xyz
```

### 7.4 OPT/FREQ/SP/热力学任务

```text
RESULT/
├── result_manifest.json
├── summary.json
├── structures/
│   └── optimized.xyz
├── energies/
│   ├── opt_energy.json
│   ├── sp_energy.json
│   └── energy_summary.json
├── frequencies/
│   ├── frequencies.json
│   └── normal_modes.json
└── reports/
    └── thermochemistry.json
```

### 7.5 机理任务

```text
RESULT/
├── result_manifest.json
├── summary.json
├── mechanism/
│   ├── reaction_network.json
│   ├── energy_profile.json
│   ├── route_summary.json
│   ├── ts_summary.json
│   └── irc_validation.json
├── structures/
│   ├── reactant.xyz
│   ├── product.xyz
│   ├── ts_01.xyz
│   └── ts_02.xyz
└── reports/
    └── mechanism_report.html
```

`mechanism/` 是结果类别，不是额外的项目层、任务层或命名规则。

---

## 8. 结果清单

每个任务的 `RESULT/result_manifest.json` 是前端结果展示的入口。

```json
{
  "version": 2,
  "task_id": "task_001",
  "workflow": "optfreqsp",
  "status": "completed",
  "products": [
    {
      "id": "optimized_structure",
      "label": "优化后结构",
      "path": "structures/optimized.xyz",
      "kind": "structure"
    },
    {
      "id": "frequency_modes",
      "label": "振动模式",
      "path": "frequencies/normal_modes.json",
      "kind": "frequency_modes"
    },
    {
      "id": "energy_summary",
      "label": "能量汇总",
      "path": "energies/energy_summary.json",
      "kind": "energy_report"
    }
  ]
}
```

服务端可读取该清单，但不复制完整结果文件。

---

## 9. 服务端与计算节点边界

### 9.1 服务端保存

服务端只保存项目和任务元数据：

```text
tasks
├── task_id
├── project_id
├── molecule_name
├── task_name
├── remark
├── display_name
├── workflow
├── task_dir_name
├── status
├── node_id
├── node_path
├── input_hash
├── result_manifest_path
├── current_stage
├── created_at
└── updated_at
```

服务端不保存以下完整文件：

- `.out`
- `.xyz`
- `.gbw`
- `.hess`
- `.engrad`
- CREST/CENSO 原始文件
- ORCA/XTB 中间文件

### 9.2 计算节点保存

节点保存：

- `input.xyz`
- `WORK/`
- `RESULT/`
- checkpoint
- 原始输出
- 中间结构
- 任务运行日志

### 9.3 节点映射

服务端需要维护：

```text
task_id
node_id
storage_mode
storage_path
last_seen
input_hash
result_manifest_mtime
```

示例：

```json
{
  "task_id": "task_001",
  "storage_node": "node_a",
  "storage_mode": "sftp",
  "storage_path": "/scratch/acp/Concented_TS_Project/XXXTS1_mechanism_route01",
  "result_manifest_path": "RESULT/result_manifest.json"
}
```

服务端通过本地访问、SSH/SFTP 或节点 Agent 按需读取文件。

---

## 10. 前端文件树

ACP 左侧默认显示项目—任务层级：

```text
项目列表
├── Concented_TS_Project
│   ├── XXXTS1_mechanism_route01
│   ├── XXXTS2_mechanism_route01
│   └── ethanol_opt_r2scan_final
└── Other_Project
    └── methanol_freq_validation
```

选中任务后显示：

```text
任务：XXXTS1_mechanism_route01
├── input.xyz
├── WORK
│   ├── 00_RUNTIME
│   ├── 02_SEARCH
│   ├── 03_OPT
│   └── 07_PATH
└── RESULT
    ├── structures
    ├── energies
    ├── trajectories
    ├── mechanism
    └── reports
```

默认行为：

- 默认展开 `RESULT`。
- 默认折叠 `WORK`。
- 空的阶段目录不显示。
- `WORK` 中的原始文件通过“全部文件”或展开操作查看。
- `RESULT` 中的文件直接进入结构、能量、频率或机理可视化窗口。

---

## 11. `.out` 解析与可视化

`.out` 文件始终保存在节点的 `WORK/` 中。

计算完成后，在节点侧解析并生成 `RESULT/` 数据：

```text
RESULT/
├── trajectories/
│   └── optimization.xyz
├── frequencies/
│   ├── frequencies.json
│   ├── normal_modes.json
│   └── ir_intensities.json
└── energies/
    └── scf_history.json
```

### 11.1 OPT 查看器

- 优化轨迹帧滑动。
- 播放和暂停优化过程。
- 查看当前步数和能量。
- 查看 RMS gradient 和最大梯度。
- 查看 SCF 收敛状态。
- 导出任意一步结构。
- 将选中结构作为新任务输入。

### 11.2 FREQ 查看器

- 频率列表。
- 虚频标记。
- 红外和拉曼强度。
- 振动模式播放。
- 位移向量缩放。
- 振动模式导出。
- 将虚频结构保存为 TS 初猜。

### 11.3 能量和热力学查看器

- SCF 能量。
- HOMO/LUMO。
- 电荷和自旋。
- ZPE。
- 热焓、熵和 Gibbs 自由能。
- 溶剂模型。
- 计算方法和基组。

---

## 12. API 方向

建议新增项目—任务级 API：

```text
GET  /api/v2/projects
GET  /api/v2/projects/{project_id}/tasks
GET  /api/v2/tasks/{task_id}
GET  /api/v2/tasks/{task_id}/tree?area=result
GET  /api/v2/tasks/{task_id}/tree?area=work
GET  /api/v2/tasks/{task_id}/files/{path}
GET  /api/v2/tasks/{task_id}/results
GET  /api/v2/tasks/{task_id}/structures/{id}
GET  /api/v2/tasks/{task_id}/frequencies/{id}
```

批量提交仍然允许，但每个数组元素必须创建一个独立任务：

```json
{
  "project_id": "project_001",
  "tasks": [
    {
      "molecule_name": "ethanol",
      "task_name": "opt",
      "remark": "final"
    },
    {
      "molecule_name": "methanol",
      "task_name": "opt",
      "remark": "final"
    }
  ]
}
```

---

## 13. 工作流到目录的映射

| 工作流 | WORK 主要区域 | RESULT 主要区域 |
|---|---|---|
| singlepoint | `05_SP/ORCA` | `energies/` |
| optimize | `03_OPT/ORCA` | `structures/`、`trajectories/`、`energies/` |
| frequency | `04_FREQ/ORCA` | `frequencies/` |
| optfreq | `03_OPT/`、`04_FREQ/` | `structures/`、`frequencies/`、`energies/` |
| optfreqsp | `03_OPT/`、`04_FREQ/`、`05_SP/`、`06_THERMO/` | `structures/`、`frequencies/`、`energies/`、`reports/` |
| ensemble | `02_SEARCH/` | `ensembles/`、`structures/` |
| energy | `02_SEARCH/`、`03_OPT/`、`05_SP/`、`06_THERMO/` | `ensembles/`、`energies/`、`structures/` |
| xtbmd_censo_energy | `02_SEARCH/`、`03_OPT/`、`06_THERMO/` | `ensembles/`、`energies/`、`structures/` |
| nmr | `02_SEARCH/`、`03_OPT/`、`05_SP/` | `reports/`、`structures/` |
| mechanism | `02_SEARCH/`、`03_OPT/`、`07_PATH/`、`08_ANALYSIS/` | `mechanism/`、`structures/`、`energies/`、`trajectories/` |

---

## 14. 迁移实施顺序

### Phase 1：定义统一布局

新增公共布局对象：

```text
TaskLayout
TaskStorage
TaskRecord
ResultManifest
NodePathMapping
```

统一计算节点路径、文件名和阶段目录。

### Phase 2：统一新任务创建

所有新任务创建：

```text
project/task/input.xyz
project/task/WORK/
project/task/RESULT/
```

停止创建旧的：

```text
Inputs/
Results/
Work/
```

### Phase 3：迁移工作流输出

将各工作流统一写入：

```text
WORK/<stage>/<engine>/
RESULT/<category>/
```

涉及：

- `src/acp/workflows/simple.py`
- `src/acp/workflows/ensemble.py`
- `src/acp/workflows/energy.py`
- `src/acp/workflows/xtbmd_censo_energy.py`
- `src/acp/workflows/nmr.py`
- `src/acp/mechanism/orchestrator.py`
- 本地和远程 runner

### Phase 4：统一节点存储访问

新增统一存储接口：

```text
TaskStorageBackend
├── LocalStorageBackend
├── SftpStorageBackend
└── NodeAgentStorageBackend
```

### Phase 5：结果解析和可视化

新增结果解析模块：

```text
src/acp/results/
├── manifest.py
├── orca_parser.py
├── xtb_parser.py
├── crest_parser.py
├── optimization.py
├── frequencies.py
└── thermochemistry.py
```

### Phase 6：兼容历史任务

- 旧任务继续使用旧目录读取逻辑。
- 新任务使用 v2 目录。
- 前端根据布局版本选择读取方式。
- 不强制移动或删除历史任务文件。
- 旧任务可在用户打开时生成只读结果索引。

---

## 15. 最终约束

最终 ACP 任务文件模型固定为：

```text
项目/
└── 分子名_任务名_备注/
    ├── input.xyz
    ├── WORK/
    │   └── 所有真实计算文件
    └── RESULT/
        └── 所有最终结果和可视化数据
```

机理任务不新增特殊目录层和特殊命名规则。

例如：

```text
XXXTS1_mechanism_route01
XXXTS2_mechanism_route01
```

与以下任务完全遵守同一目录契约：

```text
ethanol_opt_r2scan_final
methanol_freq_validation
```

差异只体现在工作流使用的阶段以及 `RESULT/` 中生成的结果类别。
