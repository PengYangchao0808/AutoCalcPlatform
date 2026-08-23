# ACP 统一结构输入与任务结果复用设计（DevDoc）

**版本**: v1 · 2026-08-22
**状态**: 📋 方案设计（本轮仅设计，未改代码）
**范围**: 前端 ACP Workbench v2 输入模块统一 + 后端 structure-sources API + 产物标记（role）

---

## 1. 背景与目标

当前前端把结构输入拆成「SMILES | 粘贴 | 上传」三个互斥页签，且必须点击「解析预览」按钮才会触发解析；简单工作流（singlepoint/freq 等）在前端被禁止使用 SMILES。这带来三个问题：

1. **输入割裂**：SMILES 与粘贴本质都是文本输入，拆成两个页签徒增心智负担；上传虽自动解析，但也没有独立预览入口。
2. **手动确认反模式**：每次解析都要点按钮；格式识别为纯前端启发式，Gaussian GJF（可能同时含 `%chk` 与 `#` 路由行）会被误判为 ORCA INP。
3. **结果无法复用**：已完成任务的最终稳定结构（`optimized.xyz` / `*_global_min.xyz`）没有统一入口载入为新任务输入，用户只能手动复制粘贴。

本轮设计目标：

- 输入统一为「结构输入（自动识别） | 任务结果 | 上传」三个来源页签，其中结构输入为单文本框 + 300–500ms 防抖自动解析；
- 删除普通结构输入的「解析预览」按钮（机制研究的「确认反应定义」按钮是反应角色确认动作，**保留**）；
- 新增「任务结果」来源页签：从最近 COMPLETED 任务一键载入最终稳定结构，自动填充电荷/自旋多重度，提交时携带来源快照与 checksum；
- 修正 `detect_format()` 识别优先级，返回 `detected_format` 供前端展示「识别为 GJF」等；
- 扩展 `result_summary.json` 产物条目增加 `role: "final_stable_structure"` 标记；
- 新增 `GET /api/v1/structure-sources/recent` 与 `GET /api/v1/structure-sources/{source_id}` 两个后端接口，远程任务由服务端按需读取最终 XYZ。

---

## 2. 现状盘点（代码坐标）

### 2.1 前端 `frontend/ACP_Workbench_v2.html`（单文件 12,984 行，唯一外部依赖 3Dmol）

| 要素 | 位置 | 现状 |
|------|------|------|
| 输入来源页签 | L2275–2279 | `SMILES \| 粘贴 \| 上传` 三个 `.input-mode-tab` |
| 三个输入面板 | L2282 / L2287 / L2292 | `#input-panel-smiles` / `#input-panel-paste` / `#input-panel-upload` |
| 简单工作流提示 | L2280 | `#simple-input-hint`「简单工作流仅接受结构文件…」 |
| 解析预览按钮 | L2303 | `#btn-parse-preview`（绑定 L12669 → `parseStructuresPreview` L10308–10361） |
| 页签切换逻辑 | L10298–10306 | `setWizardInputMode(mode)` 显隐三个面板 |
| 简单工作流隐藏 SMILES | L10511–10544 | `updateInputModeVisibility()` 隐藏 smiles 页签 + 提交时拦截 |
| 解析请求 | L10350–10354 | `POST /structures/parse {content, format, filename}`；上传走 `POST /uploads` FormData（L10335–10348） |
| 解析结果处理 | L10363–10400 | `handleParseResult(body)` → 写入 `wizardStructures` + `renderStructurePreview()` |
| 结构预览区 | L2397–2417 | `#structure-preview` **默认 `display:none`**；表格 `#preview-tbody` + 3D `#structure-preview-3d` |
| 3D 预览 | L10476–10504 | `initPreviewViewer()` / `renderPreviewStructure3D()` |
| 提交任务 | L11856–12249 | `submitJobModal()`；inputPayload 组装 L12125–12143；body L12225–12232 `{workflow,name,input,method,resources,target_node,project_id}` |
| 简单工作流 SMILES 拦截 | L12140–12143 | `source_type === "smiles"` 时 alert 并 return |
| 机制「确认反应定义」 | L2320 `#mech-preview-definition`；L8776–8817 preview；L8852–8909 confirm；L11996–11999 锁定守卫 | 反应角色确认动作，**独立于通用解析，保留** |
| 顶层区块切换 | L1786–1789 / L12275–12298 | 工作台 ↔ 机理研究 |
| i18n | zh L3408–3409 / en L3935–3936 | `modal.simple_input_hint` / `modal.simple_no_smiles` |

### 2.2 后端解析 `src/acp/intake/parsers.py`

- `detect_format(filename, content)`（L50–80）：扩展名快路径 → 内容启发式。**当前优先级问题**：
  1. `$SDG` 或（`M  END` + `$$$$`）→ sdf
  2. `M  END` → mol
  3. 首行 `%` 或前 200 字符含 `!` → **inp** ⚠️（`%chk=` 开头的 GJF 在此被误判为 ORCA INP）
  4. 首行 `#` → gjf
  5. 首字符数字 / ≥80% 原子行 → xyz
  6. 单行短文本 → smiles（兜底）
- 6 个 `parse_*_text()` 解析器返回 `StructureAsset`（`original_format` 已按格式填写，但**响应层不返回 detected_format**）。
- `parse_xyz_text` 支持从 comment 行解析 `charge=` / `mult(?)`（L234–243）——结构来源可复用该逻辑。

### 2.3 后端 API `src/acp/api/v1_routes.py` + `v1_schemas.py`

| 端点 | 位置 | 请求/响应模型 |
|------|------|---------------|
| `POST /structures/parse` | L2712–2726 | `StructureParseRequest {content, format="auto", filename}` → `StructureParseResponse {structures, errors, warnings, ok}` |
| `POST /uploads` | L2964–3027 | `UploadResponse {upload_id, filename, size, structures, errors, warnings, ok}`（`parse=true` 时立即解析并保存 normalized xyz） |
| `POST /jobs` | L1027–1079 | `V1JobCreateRequest {workflow, name, input(dict), method, resources, output_dir, config_path, tags, project_id, execution_mode, target_node}` |
| `GET /jobs` | L1081–1107 | 任务列表 |
| `GET /jobs/{id}/detail` | L1924–1946 | `V1JobDetailResponse {job, stages, artifacts_summary, error_detail, disk_state, recovery, metrics}` |

- `StructureAssetModel`（v1_schemas.py L567–582）与 intake `StructureAsset` 字段一一对应——**structure-sources GET 直接复用**。
- 错误约定：`HTTPException(status_code, detail)`，无自定义全局 handler。
- checksum 约定：内部 `"sha256:<hex>"` 前缀（`artifacts.py:133`、`provenance.py:79`）；远程文件端点返回裸 hex（`RemoteFileChecksumResponse.sha256`）。

### 2.4 调度器数据层 `src/acp/scheduler/`

- `jobs.py`：`JobStatus`（L34–63，`COMPLETED` 为终态）；`JobSpec`（L564–599，`input: dict[str, Any]` 自由承载来源）；`JobRecord`（L605–651，含 `work_dir` / `completed_at` / `remote_job_id` / `result`）。
- `store.py`：jobs 表（L26–49）；`list(status, limit, project_id, completed_before)`（L120–162）按 `created_at DESC` 排序；富列表 LEFT JOIN projects/mechanism_studies（L163–212）。**没有「最近完成」专用查询——需新增。**
- `artifacts.py`：`infer_artifact_type`（L145–164）可识别 `optimized_xyz`，但 `capture_stage_artifacts` 只扫 stage 目录，且 runner 仅捕获 `work_dir/results`（runner.py:1569）——**`<safe_name>/` 下的最终 XYZ 目前不落 artifacts 表**。结构来源不应依赖 artifacts 表。
- `projects.py`：默认项目 `uncategorized`。
- 远程判定 `_is_remote_job()`（manager.py L172–183）：`remote_job_id` 或 `result.lsf_job_id` 或 `execution_kind=="remote"`；远程目录 `result["remote_dir"]`（remote/runner.py L251/490/678）= `node.remote_work_dir/<job_id>`。
- work_dir 解析 `_resolve_work_dir()`（manager.py L1057–1079）：`<run_root>/<project_id>/<job_id>/`；runner 以 `--output <work_dir>` 传参，工作流在 work_dir 下建 `<safe_name>/` 子目录。

### 2.5 产物布局与 result_summary（`docs/ACP_Job_File_Layout_Spec.md` + 工作流代码）

- Zone A（调度器）/ Zone B（工作流产物 `work_dir/<safe_name>/`）/ Zone C（`result_summary.json` 指针文件）。
- `write_result_summary(product_root, workflow, products)`（`workflows/_helpers.py` L22–68）输出 `{version:1, workflow, products:[{label, path, kind}]}`；`path` 相对 summary 所在目录；`kind ∈ {xyz, report, table, plot, file}`。
- 现有写入点：
  - `energy_shared.py::write_final_outputs`（L785 附近，**energy/xtbmd 三工作流统一收口**）→ `RESULT/structures/{mol_name}_global_min.xyz`、`RESULT/structures/all_conformers.xyz`、`RESULT/energies/ensemble_thermo.json`、`RESULT/energies/conformer_thermo.csv`
  - `simple.py` 成功路径 6 处（L315/356/405/496/606，opt/optfreq/optfreqsp/xtb_optimize 写 `optimized.xyz`）
  - `nmr.py`（L1025，报告/图谱，无结构复用价值）
  - **`ensemble.py` 不写 result_summary.json；`mechanism` 不写**（布局规范 §5.3 标注 mechanism 为「待接入 P2」）
- 消费方：`files.py::_collect_pinned()`（L99–136）`rglob` 所有 `result_summary.json` → 重定位为相对 work_dir 的 pinned 数组，**只返回存在的文件、破损指针静默丢弃**——结构来源发现可直接复用此模式；`resolve_safe()`（L139–150）路径穿越守卫。
- ⚠️ 防回归：`result_summary.json` 是 **write-only by 工作流**，绝不参与 resume/checkpoint 判定（布局规范 §6.2）——本设计只读不写，遵守此约束。

---

## 3. 设计总览

```
┌─────────────────────────── ACP Workbench v2 (frontend) ───────────────────────────┐
│ 新建任务弹窗 (#job-modal)                                                          │
│ ┌─ 来源页签 ────────────────────────────────────────────────────────────────────┐ │
│ │ [结构输入（自动识别）] [任务结果] [上传]                                        │ │
│ └────────────────────────────────────────────────────────────────────────────────┘ │
│ 结构输入面板:  [文本输入框 (统一 SMILES/XYZ/MOL/SDF/GJF/COM/INP)]                  │
│               [识别格式: SMILES/GJF/...] [解析状态: 正在解析/成功/失败]            │
│               [强制格式: auto ▾]（高级选项，处理歧义）                             │
│ 任务结果面板:  [当前项目/全部项目] [搜索任务名称] [最近完成] [可复用结构列表]      │
│ 上传面板:     [拖拽/选择文件 → 立即自动解析]                                       │
│ ┌─ 统一结构预览区（默认展开）─────────────────────────────────────────────────────┐ │
│ │ 结构列表表格 + 3D 预览（#structure-preview，载入任务结果时自动填充）            │ │
│ └────────────────────────────────────────────────────────────────────────────────┘ │
│ [电荷] [自旋多重度]  ← 任务结果载入时自动填充                                     │
└───────────────────────────────────────────────────────────────────────────────────┘
          │                          ▲
          │ POST /structures/parse   │ GET /structure-sources/{id} → StructureAsset
          │ (auto 格式识别)           │ (含 checksum)
          ▼                          │
┌────────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI /api/v1                                                                     │
│  · POST /structures/parse   → detect_format() 修正后返回 detected_format           │
│  · GET  /structure-sources/recent    → 最近 COMPLETED 任务的可复用结构列表          │
│  · GET  /structure-sources/{id}      → 解析最终 XYZ → StructureAsset + checksum     │
│  · POST /jobs (input.source_ref 携带 {origin, job_id, path, checksum})              │
└────────────────────────────────────────────────────────────────────────────────────┘
          │ 提交新任务: source_type="xyz_text" + source=快照XYZ + source_ref
          ▼
Runner.materialize_job_input → inputs/input.xyz（已有 xyz_text 分支，零改动）
```

**核心原则**：磁盘即真相。结构来源发现复用 `_collect_pinned` 的「rglob result_summary.json → 过滤 → resolve_safe 校验」模式，不引入新表；快照发生在**提交时刻**（把最终 XYZ 文本 + checksum 固化进新任务的 `input`），即使原任务之后被清理，新任务依然完整可算、可溯源。

---

## 4. Part A — 统一输入模块

### 4.1 后端：`detect_format()` 优先级修正（parsers.py L50–80）

**问题**：Gaussian GJF 可同时含 `%chk=`（首行）与 `#` 路由行；当前先查 `%`→inp 再查 `#`→gjf，导致 `%chk` 开头的 GJF 被误判为 ORCA INP。

**新识别顺序**（扩展名快路径 → 内容启发式，逐级降级）：

| 优先级 | 判定规则 | 返回 |
|--------|----------|------|
| 0 | 扩展名 `.xyz/.sdf/.sd/.mol/.gjf/.com/.inp` | 对应格式 |
| 1 | 首行 `$SDG`，或（`M  END` 且 `$$$$`） | sdf |
| 2 | `M  END` | mol |
| 3 | 首个非空行是数字，或 ≥80% 非空行为原子行（`_looks_like_atom_coordinates`） | xyz |
| 4 | 存在以 `#` 开头的路由行 **且** 至少 2 个空行分隔符（`parse_gjf_text` 的硬性要求）→ gjf；**`%chk=`/`%mem=`/`%nprocshared=` 开头也归入此分支**（结合后续 `#` 路由行判定） | gjf |
| 5 | 首行 `%` 或前 200 字符含 `!`，且含 `* xyz` / `* int` 块标记（ORCA 特征）；**不含 `#` 路由行** | inp |
| 6 | 单行、< 80 字符、无换行（交 RDKit 验证） | smiles |
| 7 | 兜底 | smiles（解析失败则返回错误「无法识别格式」） |

**实现要点**：

1. **「识别 + 验证」两步**：detect 选候选格式 → `parse_structure_text` 验证；若候选解析报错且存在下一候选，则降级重试，返回**最终成功**的格式作为 `detected_format`（GJF 与 INP 的 `%` 歧义即靠此消解）。
2. **GJF/INP 消歧规则**（核心）：
   - 全文出现 `#` 路由行 + 2 个以上空行 → gjf（即使带 `%chk` 头）；
   - 含 `* xyz` / `* int` 块标记且无 `#` 路由行 → inp；
   - 仅 `%` 头（如 `%pal nprocs 8 end`）无 `#`、无 `*` 块 → 先试 gjf 再试 inp，取成功者。
3. **`.txt` 不再强制映射 smiles**（当前 L60 `"txt": "smiles"`），改为落入内容检测，避免上传 `foo.txt`（内含 XYZ）被误判。
4. `detect_format` 保持对外签名不变（`filename, content -> str`），新增内部 helper（如 `_looks_like_gjf(content)` / `_looks_like_orca_inp(content)`）承载规则 4/5。

### 4.2 后端：解析响应返回 `detected_format`

- `v1_schemas.py`：`StructureParseResponse` 增加 `detected_format: str | None = None`（L591–595）；`UploadResponse` 同样增加（L598–605）。**纯增量字段，向后兼容**。
- `v1_routes.py::parse_structures`（L2712–2726）：`fmt = "auto"` 时执行上述降级识别，并把最终采用的格式写入 `detected_format`；`UploadResponse` 在 L3007–3009 处同样记录。

### 4.3 前端：页签与输入框合并

| 变更 | 位置 | 说明 |
|------|------|------|
| 页签替换 | L2275–2279 | 三个 tab 改为 `data-input-mode="structure" \| "results" \| "upload"`；文本：`结构输入（自动识别） \| 任务结果 \| 上传` |
| 面板重构 | L2282 / L2287 / L2292 | `#input-panel-structure`（统一 textarea，复用 `#modal-smiles` 的 DOM 元素并改 id 为 `#modal-structure-input`，同时兼容读取 `#modal-paste` 的值——两处 value 合并归一）；`#input-panel-results`（任务结果，见 Part B）；`#input-panel-upload`（保留） |
| 删除提示 | L2280 | 移除 `#simple-input-hint`（SMILES 前端禁令取消，见 4.5） |
| 删除解析预览 | L2303 + L12669 | 移除 `#btn-parse-preview` 元素与事件绑定 |
| 防抖自动解析 | 新增 | `debounce(parseStructuresPreview, 350)` 绑定 textarea `input` 事件；空输入不请求，预览区显示「等待输入」；上传文件选择后立即触发（不防抖） |
| 竞态保护 | 新增 | 模块级 `parseSeqToken`，请求发出时自增；响应返回时仅当 token 为最新才应用结果（防止慢响应覆盖新输入） |
| 强制格式 | 新增 | `#modal-format` 下拉 `auto/SMILES/XYZ/MOL/SDF/GJF/INP`，默认 `auto`；选中非 auto 时请求 `format` 字段直接透传 |
| 识别格式展示 | 新增 | `#detected-format-chip` 显示 `识别为 {detected_format.toUpperCase()}`；解析状态行 `#parse-status` 显示 正在解析 / 解析成功（N 个结构）/ 解析失败（错误摘要） |
| 预览默认展开 | L2397 | `#structure-preview` 去掉 `display:none`，初始渲染为「等待输入」空态；保留 `renderStructurePreview` 的显隐控制用于异常场景 |

### 4.4 解析请求归一（`parseStructuresPreview` 重构，L10308–10361）

```
输入来源:
  structure 模式 → content = 统一 textarea 值, fmt = 强制格式或 "auto", filename = ""
  upload 模式   → POST /uploads（已有自动解析路径, 立即执行）
请求: POST /structures/parse {content, format: fmt, filename}
响应: detected_format + structures → handleParseResult 追加格式 chip 渲染
```

### 4.5 取消简单工作流 SMILES 前端禁止（保持语义一致）

- 删除 `updateInputModeVisibility()` 中隐藏 smiles 页签的逻辑（L10511–10517）与 `#simple-input-hint`；
- 删除 `submitJobModal()` 中 `isSimpleWorkflowSelected() && source_type === "smiles"` 的拦截（L12140–12143）；
- 删除对应 i18n key（`modal.simple_input_hint` / `modal.simple_no_smiles`，zh L3408–3409 / en L3935–3936）；
- **行为保证**：SMILES 输入经解析器 ETKDG 嵌入生成 XYZ（已有 `parse_smiles_list`），提交时走 `source_type="xyz_text"` 分支（见 7.3），后端 runner 的 `_materialize_single_input` 已支持 xyz_text → `inputs/input.xyz`，**零后端改动**即实现「SMILES 自动生成 XYZ 后继续计算」。

---

## 5. Part B — 「任务结果」模块（前端页签）

### 5.1 页签结构

`#input-panel-results` 面板布局：

```
任务结果
├── 项目过滤: [当前项目 ▾ / 全部项目]      （默认跟随弹窗已选 project）
├── 搜索:     [🔍 搜索任务名称...]
├── 最近完成  (GET /structure-sources/recent?project_id=&limit=50)
└── 可复用结构列表（每行一个 source）:
     任务名称 · 工作流徽标 · 完成时间 · 项目
     结构名称(label) · 分子式 · 原子数 · 电荷 · 自旋多重度
     [本地/远程] 徽标 · [载入] 按钮
```

### 5.2 交互流程（选择 → 载入 → 提交）

1. 页签打开时请求 `GET /api/v1/structure-sources/recent`（携带当前 `project_id`，默认当前项目；切「全部项目」重发请求）；搜索框 300ms 防抖对**前端已加载列表**过滤（v1 不做服务端搜索）。
2. 用户点「载入」→ `GET /api/v1/structure-sources/{source_id}`：
   - 返回 `StructureAsset`（xyz/molfile/has_3d/charge/multiplicity/atom_count/formula）→ **复用现有 `renderStructurePreview()`** 填充统一预览区；
   - 自动填充 `#modal-charge` 与 `#modal-mult`；
   - 将 asset 包装进 `wizardStructures`，并附加 `s.source_ref = {origin:"job_artifact", job_id, path, checksum}`（checksum 取自 GET 响应）。
3. 用户切换计算方法 → 提交新任务（走标准 `submitJobModal()`，Part D 负责 source_ref 透传）。
4. 载入失败（文件被清理 / 远程节点不可达）→ 列表行内错误提示 + 该 source 标记失效，不阻塞其他行。

### 5.3 排除规则（服务端保证）

只返回：`status == COMPLETED`、能找到最终结构文件、结构可正常解析。排除：`singlepoint`/`frequency`（无几何结构）、失败/取消任务、无几何结构的任务。工作流 → 默认结构映射见 Part C 的发现规则表。

---

## 6. Part C — 后端接口设计

### 6.1 发现服务 `src/acp/scheduler/structure_sources.py`（新增）

```
class StructureSourceService:
    def __init__(self, store: JobStore, run_root: Path,
                 fetcher: RemoteResultFetcher | None = None)
    def list_recent(self, *, limit=20, project_id=None,
                    workflow=None, include_remote=True) -> list[StructureSourceSummary]
    def get(self, source_id: str) -> StructureSourceDetail   # asset + checksum
    # 内部:
    _discover_job(job) -> list[SourceCandidate]              # 本地 rglob result_summary
    _probe_remote(job) -> list[SourceCandidate] | None       # 远程清单探测（TTL 缓存）
    _fetch_remote_xyz(job, rel_path) -> str | None           # SFTP 按需读取
```

**发现规则（`_discover_job`）**——复用 `files.py::_collect_pinned` 模式：

1. `rglob(work_dir, "result_summary.json")`（`_RESULT_SUMMARY_FILENAME` 常量复用）；
2. 对每个 summary，在 `products` 中选结构：
   - **首选** `role == "final_stable_structure"` 且 `kind == "xyz"`；
   - **旧任务回退**（无 role）：`kind == "xyz"` 且文件名匹配 `optimized.xyz` / `*_global_min.xyz` / `finalDFT/all_conformers.xyz`（取首帧）/ `ensemble.xyz`（取首帧，rank-1）；
3. 用 `resolve_safe(work_dir, rel_path)` 校验存在性与路径安全，破损指针静默丢弃；
4. 读取 XYZ 文本 → `parse_xyz_text` 取首帧 → 得 `formula/atom_count/charge/multiplicity`（charge/mult 优先级：XYZ comment `charge=/mult=` → 无则取 `job.spec.input.charge/multiplicity` → 兜底 `0,1`）。

**source_id 契约**：`job_<job_id>:<相对 work_dir 的路径>`，正斜杠。示例：`job_20260822_001_energy:ethanol/ethanol_global_min.xyz`。天然稳定、可逆解析、可读性好。

### 6.2 工作流 → 默认结构映射（含角色优先级）

| 工作流 | 默认加载结构 | role 标记 | 旧任务回退 |
|--------|--------------|-----------|-----------|
| optimize / optfreq / optfreqsp / xtb-opt | `optimized.xyz` | `final_stable_structure` | 文件名匹配 |
| energy / xtbmd_censo_energy | `{mol_name}_global_min.xyz` | `final_stable_structure` | `*_global_min.xyz` |
| ensemble | `ensemble/ensemble.xyz` 首帧（rank-1） | 不新增（无 result_summary） | `ensemble.xyz` 首帧 |
| mechanism | 稳定态结构（可展开选择） | **P2**（依赖布局规范 §5.3 的 orchestrator study-complete 钩子） | 无 |
| singlepoint / frequency | ❌ 不提供结构复用 | — | — |

> mechanism 依赖 `write_result_summary` 在 orchestrator study-complete 钩子接入（布局规范已标注「待接入 P2」），v1 明确不在范围，文档中标注扩展点。

### 6.3 Schema（`v1_schemas.py` 新增）

```python
class StructureSourceSummary(BaseModel):
    source_id: str                       # "job_<id>:<rel_path>"
    job_id: str
    job_name: str
    workflow: str
    project_id: str | None = None
    completed_at: str = ""
    label: str = ""                      # product.label（如 "Global minimum structure"）
    path: str = ""                       # 相对 work_dir
    formula: str = ""
    atom_count: int = 0
    charge: int = 0
    multiplicity: int = 1
    has_3d: bool = True
    remote: bool = False
    needs_fetch: bool = False            # 远程且未探测到文件的占位标记

class StructureSourceListResponse(BaseModel):
    sources: list[StructureSourceSummary] = Field(default_factory=list)

class StructureSourceDetailResponse(BaseModel):
    source_id: str
    checksum: str | None = None          # "sha256:<hex>"（复用 artifacts.compute_checksum 约定）
    structure: StructureAssetModel        # 直接复用现有模型 → 前端预览组件零适配
```

`StructureParseResponse` / `UploadResponse` 增加 `detected_format: str | None = None`（Part A）。

### 6.4 端点（`v1_routes.py` 新增，挂 `/api/v1`）

```
GET /api/v1/structure-sources/recent
    query: project_id? (默认当前项目) | all_projects=true | workflow? | limit (默认20, max50) | include_remote (默认true)
    → StructureSourceListResponse

GET /api/v1/structure-sources/{source_id}
    → StructureSourceDetailResponse | 404 {"detail": "Source not found: ..."}
```

**`recent` 实现**：

1. `store.list_recent_completed(...)`（store.py 新增，见 6.5）取 COMPLETED 任务，按 `completed_at DESC`；
2. 逐 job `_discover_job`（本地 rglob）；远程 job 走 `_probe_remote`（bounded，见 6.6），探测失败降级为 `remote:true, needs_fetch:true`；
3. 限制返回条数；`workflow` 过滤支持 `singlepoint/frequency` 直接返回空。

**`get` 实现**：

1. 解析 `source_id` → `job_id` + `rel_path`（格式非法 → 404）；
2. 读 job；`status != COMPLETED` → 404；
3. 本地：`resolve_safe` 取绝对路径 → `compute_checksum(path)` + `parse_xyz_text` → `StructureAsset`；
4. 远程：`fetcher` 按 `result["remote_dir"] + rel_path` SFTP 读取文本（路径 POSIX 规范化防穿越）→ 同 3；节点不可达 → 404 `{"detail": "remote node unavailable: ..."}`；
5. 成功返回 `StructureSourceDetailResponse`。

### 6.5 `store.py` 新增查询

```python
def list_recent_completed(self, limit: int = 20, *,
                          project_id: str | None = None,
                          workflow: str | None = None,
                          completed_after: str | None = None) -> list[JobRecord]:
    # SELECT * FROM jobs
    #  WHERE status='completed' [AND project_id=?] [AND workflow=?] [AND completed_at>=?]
    #  ORDER BY completed_at DESC LIMIT ?
```

复用 `_row_to_record()`。**唯一新增 SQL**，不迁移 schema。

### 6.6 远程任务处理

- 复用现有设施：`RemoteResultFetcher`（`remote/fetcher.py`）+ `record.result["remote_dir"]` + `_is_remote_job()`。
- **列表探测**：远程 job 数量 ≤ 阈值（如 10）时并行探测 `result_summary.json` 是否存在（SFTP stat），结果 30s TTL 缓存（对齐 `node_manager` 30s 模式）；超过阈值或节点不可达 → `needs_fetch:true` 占位，不阻塞列表。
- **按需读取**：GET 时服务端 SFTP 读最终 XYZ → 解析 → 返回；**前端不接触任何 SSH/SFTP 逻辑**，本地/远程统一走同一接口（满足验收标准「远程任务和本地任务在界面上使用相同的结构加载流程」）。
- 远程取回的 XYZ 是否写回本地 work_dir：v1 不写回（保持「磁盘即真相」，避免污染本地布局）；如需缓存可后续在 P2 引入带 TTL 的临时快照。

---

## 7. Part D — 产物标记与来源快照

### 7.1 `result_summary.json` 增加 `role`（向后兼容）

- `_helpers.py::write_result_summary`（L22–68）透传 `role` 字段（`item.get("role")`），未提供时不写；`version` 保持 1（`role` 为可选增量字段）。
- **写入点**：
  - `energy_shared.py::write_final_outputs`（L785 附近）：`{mol_name}_global_min.xyz` 条目加 `"role": "final_stable_structure"`；
  - `simple.py` 4 处 `optimized.xyz` 条目（L356/405/496/606）加 `"role": "final_stable_structure"`；
  - 其余产物不加 role。
- `files.py::_collect_pinned` 只读 `label/path/kind`，`role` 透传无影响；本设计**只读** result_summary，遵守布局规范 §6.2（write-only by 工作流，绝不参与 checkpoint）。

### 7.2 提交快照（source_ref）

前端选择任务结果后，提交 body 的 `input` 变为：

```json
{
  "source_type": "xyz_text",
  "source": "<最终结构的完整 XYZ 文本（快照）>",
  "charge": 0,
  "multiplicity": 1,
  "source_ref": {
    "origin": "job_artifact",
    "job_id": "20260822_001_energy",
    "path": "results/mol/finalDFT/mol_global_min.xyz",
    "checksum": "sha256:..."
  }
}
```

- `source_ref` 为**自由扩展字段**（`JobSpec.input` 是 `dict[str, Any]`），**零 schema 迁移**；随 `spec_json` 持久化，任务详情可读。
- 快照语义：`source` 是完整 XYZ 文本——原任务之后被 purge 也不影响新任务；`source_ref` 仅作溯源展示。
- **runner 零改动**：`_materialize_single_input` 已支持 `source_type == "xyz_text"` → `inputs/input.xyz`。
- 前端组装点：`submitJobModal()` 的 inputPayload（L12125–12143）在 `xyz_text` 分支追加 `source_ref: s.source_ref`（载入任务结果时由 Part B 挂到 structure 对象上）。

### 7.3 提交载荷归一（统一三种来源）

| 来源 | input.source_type | source | 备注 |
|------|-------------------|--------|------|
| 结构输入（自动识别） | smiles / xyz_text / structure_asset | 原样 | 现有逻辑保留；SMILES 经嵌入生成 XYZ 后仍可提交 |
| 上传 | structure_asset（normalized_path） | 原样 | 现有逻辑保留 |
| 任务结果 | **xyz_text** | 快照 XYZ | 新增 `source_ref` |

---

## 8. 前端变更清单汇总（ACP_Workbench_v2.html）

| # | 变更 | 位置 | 说明 |
|---|------|------|------|
| 1 | 页签替换 | L2275–2279 | 三来源页签 |
| 2 | 统一 textarea | L2282（替换 smiles/paste 两面板） | 自动识别输入 |
| 3 | 任务结果面板 | 新增 `#input-panel-results` | Part B |
| 4 | 删除解析预览按钮 | L2303 + L12669 | 防抖自动解析替代 |
| 5 | 预览默认展开 | L2397 | 去掉 `display:none`，初始「等待输入」 |
| 6 | 强制格式下拉 | 新增 `#modal-format` | 高级选项 |
| 7 | 识别格式/状态行 | 新增 chip + `#parse-status` | detected_format 展示 |
| 8 | 删除 SMILES 禁令 | L10511–10544 + L12140–12143 + i18n | 简单工作流允许 SMILES |
| 9 | source_ref 透传 | L12125–12143 | xyz_text 分支追加 |
| 10 | 任务结果载入流程 | 新增 `loadStructureSource(id)` | GET + 填充预览/charge/mult |
| 11 | i18n 新 key | zh L3408 区块 / en L3935 区块 | tab 标签、解析状态、结果面板文案 |
| 12 | 竞态 token + 防抖 | 新增 | `debounce` + `parseSeqToken` |

> 机制研究「确认反应定义」流程（L2320 / L8776–8909 / L11996–11999）**保持不动**——它是反应角色确认动作，不是普通解析按钮。

---

## 9. 实施顺序

| 阶段 | 内容 | 产出 | 依赖 |
|------|------|------|------|
| P0 | `detect_format` 优先级修正 + `detected_format` 返回 | parsers.py + v1_schemas.py + v1_routes.py + 测试 | — |
| P1 | result_summary `role` 标记 | `_helpers.py` + `energy_shared.py` + `simple.py` + 测试 | — |
| P2 | 发现服务 + store 查询 | `structure_sources.py` + `store.list_recent_completed` + 测试 | P1（role 优先）+ P0（解析） |
| P3 | API 端点 | v1_routes.py + v1_schemas.py + 测试 | P2 |
| P4 | 前端统一输入 | 页签/防抖/删按钮/预览默认展开/强制格式/i18n | P0（detected_format） |
| P5 | 前端任务结果页签 + source_ref 提交 | 载入流程 + 提交载荷 | P3 + P4 |
| P6 | 远程按需读取 | `_probe_remote` / `_fetch_remote_xyz` + 端点远程分支 | P2/P3 |
| P7 | mechanism 结构复用（P2 扩展） | orchestrator study-complete 钩子 → result_summary 稳定态 | 布局规范 §5.3 |

> P0–P3 为后端，可独立先行合入（向后兼容）；P4–P6 为前端联调；P7 明确标注为后续扩展。

---

## 10. 验收标准映射

| # | 验收标准 | 对应设计 |
|---|----------|----------|
| 1 | 粘贴 CCO 自动识别为 SMILES，出现 3D 预览 | 4.3 防抖自动解析 + 4.4 归一请求（P4） |
| 2 | 粘贴 Gaussian GJF 自动识别为 GJF | 4.1 detect_format 优先级（P0） |
| 3 | 粘贴 XYZ/SDF/MOL/ORCA INP 无需点击即可预览 | 4.3（P4）+ 4.1（P0） |
| 4 | 新建任务窗口预览区默认打开 | 4.3 L2397 默认展开（P4） |
| 5 | 界面不再出现普通结构输入的「解析预览」按钮 | 4.3 删除 L2303/L12669（P4） |
| 6 | 可从最近完成的优化/能量任务加载最终稳定结构 | Part B + Part C（P2/P3/P5） |
| 7 | 载入后可直接切换计算方法并提交新任务 | 5.2 流程 3 + 7.2 source_ref（P5） |
| 8 | 新任务详情中可见来源任务、来源文件和 checksum | 7.2 source_ref 随 spec_json 持久化（P5） |
| 9 | 远程与本地任务使用相同结构加载流程 | 6.6 服务端按需读取（P6） |
| 10 | 简单工作流 SMILES 自动生成 XYZ 后继续计算 | 4.5（P4） |

---

## 11. 测试计划

| 测试文件 | 覆盖 |
|----------|------|
| `tests/test_acp_intake_detect_format.py`（新） | `%chk`+`#` 的 GJF → gjf；`%pal`+`* xyz` → inp；`! wb97x` ORCA → inp；SDF/MOL/XYZ/SMILES 正判；`.txt` 内容检测；歧义降级（识别+验证两步） |
| `tests/test_acp_structure_sources.py`（新） | 发现规则（role 优先 + 旧任务文件名回退）；`singlepoint/frequency` 排除；`resolve_safe` 路径穿越拒绝；charge/mult 优先级（comment > spec.input > 0,1）；source_id 可逆解析 |
| `tests/test_acp_api_structure_sources.py`（新） | recent（COMPLETED 过滤、project/workflow 过滤、limit、completed_at DESC）；get（成功解析 + checksum + 404 分支：非法 source_id / 非 COMPLETED / 文件被清理 / 远程不可达） |
| `tests/test_acp_api_v1.py`（扩展） | `StructureParseResponse.detected_format` 断言 |
| `tests/test_acp_workflows_energy.py` / `test_acp_workflows_simple.py`（扩展） | result_summary 产物含 `role: "final_stable_structure"` |
| `tests/test_acp_api_job_submit.py`（新或扩展） | 提交 `input.source_ref` → spec_json 持久化 → 任务详情可见 |
| 前端（无测试基建，人工/Playwright 清单） | 防抖（连续输入只发一次请求）、空输入不发请求、竞态 token、预览默认展开、删除按钮回归、任务结果载入 → charge/mult 填充 → 提交 |

---

## 12. 风险与防回归约束

1. **`detect_format` 行为变更**：影响 `POST /structures/parse` 与 `POST /uploads`（均走 detect）。P0 单独合入并用测试锁定；ORCA 带 `!` 无 `%` 的输入仍需正确识别（规则 5 保留 `!` 检查）。
2. **`_SCHEDULER_MARKERS`（simple.py）**：本设计不向 work_dir 注入任何调度器文件，`result_summary.json` 仍由工作流写、发现服务只读——无新增 marker 需求。
3. **result_summary 只读约束**：structure-sources 服务绝不写 result_summary，绝不参与 resume/checkpoint 判定（布局规范 §6.2）。
4. **远程探测成本**：列表端点对远程 job 的探测必须 bounded（阈值 + TTL 缓存 + 降级占位），防止 N 次 SFTP 拖垮列表响应。
5. **路径安全**：本地用 `resolve_safe`，远程用 POSIX 规范化 + `remote_dir` 前缀校验，杜绝穿越。
6. **i18n 完整性**：删除 `modal.simple_no_smiles` 等 key 时须同步清理 zh/en 两套，避免残留引用。
7. **机制研究流程隔离**：「确认反应定义」按钮、锁定守卫（L11996–11999）、SR 审核流程一律不动。
8. **防回归基准**：P0–P3 为纯后端增量，`pytest tests/ -m "not slow"` 全绿再进 P4 前端改动。

---

## 13. 待确认决策（可选项）

1. **远程列表探测阈值**：默认 ≤10 个远程 job 做探测，超过直接 `needs_fetch` 占位——是否可接受？
2. **ensemble.xyz 首帧作结构源**：ensemble 无 result_summary，v1 用文件名回退取首帧（rank-1）。若希望更严谨，P2 可为 ensemble 补写 result_summary。
3. **搜索交互**：v1 仅前端过滤已加载列表（≤50 条）；是否需要服务端分页/搜索？
