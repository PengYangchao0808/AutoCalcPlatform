# nmr-report（A-52 / A-53，P18 NMR 报告）

只读的 NMR `nmr_report.json` 查看器：候选列表、DP4/DP5 指标、归属表、观测峰认领与
本地关联高亮。**模块不计算 DP4/DP5、不重新排名、不补造分数**；缺失概率显示 `—`，
没有报告/没有候选/没有 DP4-DP5 数据时进入显式空态。

- 模块 id：`nmr-report`；唯一全局 `window.ACPNextModules["nmr-report"]`
- 经典浏览器脚本：无构建、无 ESM、无直接 `fetch`/XHR
- 计划依据：`docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md`
  - A-52（386 行）NMR 报告渲染（DP4/DP5、归属高亮）
  - A-53（387 行）多候选切换（generation 守卫）
  - BE-GAP-04（451 行）无 NMR 谱图数据端点，经典页读文件 → 本模块同样读文件端点

## 文件

| 文件 | 职责 |
|---|---|
| `module.js` | descriptor + factory；四态、报告读取/解包、候选切换、归属高亮、只读几何摘要、generation/token 守卫、dispose |
| `README.md` | 本文件：契约、端点、数据字段、i18n、接入步骤、缺口 |
| `../../css/modules/nmr-report.css` | 仅 `.acp-nmr*`；颜色/圆角/控件高度取 `base.css` / `shell.css` 变量 |
| `../../module_tests/acp2_nmr_report.test.js` | 30 项零依赖 vm 测试（fake DOM / fake request / fake context），`window.fetch` 未定义 |

## 模块契约

```js
ACPNextModules["nmr-report"] = {
  descriptor: {
    id: "nmr-report",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["nmr.report"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["nmr.report"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.nmr.errorTitle", messageKey: "next.nmr.errorMessage" },
  },
  factory: function (deps) { /* ModuleInstance */ },
};
```

`ModuleInstance`：`mount(hostEl, api): Promise<void>` · `updateContext(snapshot)` ·
`suspend()` · `resume()` · `dispose()`；另提供 `refresh()`（宿主/宿主按钮复用）与
`snapshot()`（只读检查缝：report/reportPath/selectedIndex/activeTab/selection/emptyReason/errorCode）。

`deps`：

| 键 | 形状 | 说明 |
|---|---|---|
| `doc` | `Document` | 缺省 `window.document`；测试注入 fake DOM |
| `request` | `(path, opts) => Promise<any>` | **注入的请求适配器**，返回已解析 body；缺省回退 `deps.client.transport.request` → `window.ACPClient.transport.request`。没有该通道时进入 error 态且不发请求 |
| `client` | `{ transport: { request(path, opts) } }` | `deps.request` 缺失时使用 |
| `jobDetail` | `{ fetchDetail(jobId) }` | 可选；否则用 `window.ACPClient.jobDetail`。只用于读 `spec.workflow` 与 `node_id`（远程/本地），是既有 `GET /jobs/{id}` 客户端包装；缺失时按本地任务读取 |
| `context` | `{ getSnapshot(), subscribe(fn) }` | 缺省由 `mount(host, api)` 的 `api.context` 提供；`snapshot.taskRef.jobId` 是数据源 |
| `apiBase` | `string` | 缺省 `window.API_BASE` → `/api/v1` |
| `jobId` / `projectId` / `isRemote` / `remote` | `string` / `boolean` | context 缺失或无 `taskRef` 时的显式兜底；`jobDetail` 可用时以服务端 `node_id` 为准 |

## 使用的端点（全部既有；没有新增/虚构路由）

> **前提更正**：`src/acp/api/v1_science.py` 只有
> `GET /api/v1/science/tasks/{job_id}/attempts/{attempt}/status`；
> `src/acp/api/v2_science.py` 只有 capabilities/capability-forms/preflight/attempt view/
> artifact read。两者**都没有 NMR/report 路由**。本计划线要求的 NMR 报告数据实际由
> 既有 job-files 内容端点提供（计划 §2.2 第 78 行、BE-GAP-04 明确“文件端点 + fixture”）。

| 方法/路径 | 用途 | 响应形状（实际依赖字段） |
|---|---|---|
| `GET /api/v1/jobs/{job_id}/files/{path}`（`v1_routes.py:6643`） | 本地读取报告/几何 | `.json` → 解析后的对象；`.xyz` → 文本 |
| `GET /api/v1/jobs/{job_id}/remote-files/{path}/preview?mode=report`（`v1_routes.py:6889`） | 远程读取报告 | `{ path, mode, content: { type:"json_report", file_path, report } }`；`content.type === "unknown_report"` 视为无报告 |
| `GET /api/v1/jobs/{job_id}/remote-files/{path}/preview?mode=structure` | 远程读取候选几何 | `{ content: "<xyz 文本>" }` |
| `GET /api/v1/jobs/{job_id}`（经 `ACPClient.jobDetail.fetchDetail`） | 可选：判定 `workflow`/`node_id`（远程 vs 本地） | `detail.job.spec.workflow`、`detail.job.node_id` |

报告路径探测顺序（首个命中即用；404/405 继续，其它错误 → error）：

1. `RESULT/reports/nmr_report.json`（当前 `acp.workflows.nmr` 实际布局；`nmr.py:3669`
   的 `storage.result_category_dir("reports")`，写入 `cccp/analysis/nmr/report.py:172`）
2. `reports/nmr_report.json`（历史 manifest 拼写）
3. `nmr_report.json`（历史根布局）

候选几何路径（只读展示用，非 A-52 必需数据）：`WORK/02_SEARCH/<candidate.label>/conformers/input.xyz`
→ 回退 `input.xyz`。没有几何时结构 tab 显示“未找到候选几何”，不影响报告/归属。

## 报告数据字段（schema v2，`NmrReport.as_dict` / `CandidateResult.as_dict`）

本模块消费并显示：

- 顶层：`schema_version`（缺失 = 历史报告提示；≠2 = 未知 schema 提示）、`summary`
  （`n_candidates`/`winner{index,label,dp4,dp5}`/`dp4_ranking`/`nuclei`）、
  `candidates[]`、`config`、`error_model`、`dp5_mode`、`protocol_id`、`note`、
  `processing_quality[]`、`diagnostics{conflict_matrix,leave_one_signal_out}`。
- 候选：`index`、`label`、`dp4_probability`、`dp5_probability`、`probability{dp4,dp5}`
  （`status/probability/mode/calibration_status/model_id/reasons`）、`evidence`、
  `coverage`、`ensemble_quality`、`regression{}`、`assignment[]`、`conformers[]`、
  `n_conformers`、`diagnostics`、`analysis_status`。
- 归属行：`atom`、`element`、`exp_ppm`、`calc_ppm`、`scaled_ppm`、`residual`、
  `observation_id`、`signal_group.atom_uids`。
- 观测峰认领：`diagnostics.conflict_matrix.claims[]`（`observation_id/nucleus/exp_ppm/
  candidate_indices/atom_labels/n_claims/conflict`）；缺失时按归属行 `observation_id`
  归并显示（仅展示聚合，不计算分数）。

## 四态与生命周期

| 状态（`data-acp-module-state`） | 触发 |
|---|---|
| `empty` | 无任务（`no_job`）、非 NMR 任务（`not_nmr`）、报告缺失（`no_report`）、无候选（`no_candidates`）、无 DP4/DP5 概率（`no_dp4_data`）、无谱图/归属（`no_spectrum_data`） |
| `loading` | 初始读取 / 刷新 / 重试进行中 |
| `ready` | 报告已有候选且至少一个 DP4/DP5 概率（或 server summary winner 值） |
| `error` | 无请求通道（`no_transport`）、401/403（`forbidden`）、非法 JSON（`invalid_report`）、其它读取失败（`read_failed`）；显示原因 + 重试 |

- `data-acp-nmr-empty-reason` / `data-acp-nmr-error-code` 给出稳定机器码。
- `generation` 在 `mount` / 任务切换 / dispose 递增；报告读取用 `(generation, loadToken)`，
  候选切换用 `(generation, selectToken)`，几何读取再加 `geometryToken`。任何迟到响应
  （旧任务、旧候选、旧几何）都在写 DOM 前被丢弃。
- `dispose`：置 disposed、作废全部 token、退订 context、移除所有监听（含 `acp:langchange`）、
  清空 host 与 ui/report/candidates/selection；幂等。
- `suspend`/`resume`：挂起时 `renderReady` 不写 DOM，`resume` 重渲染当前状态。

## A-52 显示规则（服务端值，不本地计算）

- **候选列表**：按 `report.candidates` 原顺序；每项显示 label、DP4、DP5、分析状态；
  只有 `summary.winner.index` 对应的候选带“胜出”标记。**不本地排序/不本地选胜者**。
- **指标**：DP4/DP5 flat 值与 typed `probability` 的 status/calibration/mode/model/reasons、
  evidence、coverage、regression（`r2_regression` 缺失时回退同义 `r_squared`，不重算）、
  ensemble quality、processing quality、诊断计数。缺失显示 `—` 并写 warnings。
- **归属表**：atom/element/exp/calc/scaled/residual/observation/claim atoms；数值文本为
  显示格式，`data-value` 保留服务端原值；残差按 `scaled − exp` 只做正负着色。
- **高亮**：点击归属行 → 同观测/同 `signal_group.atom_uids` 的行标记 `active`/`related`；
  点击观测峰行 → 该 `observation_id` 的归属行高亮；点击结构原子 chip → 对应归属行高亮。
  高亮纯本地视图状态，不写报告、不触发重新计算。
- **空态**：没有报告/候选/概率/谱图时给出原因与“不会估算/补造/本地计算 DP4/DP5”的显式文案；
  不会出现 0 分、假胜者或占位峰表。
- **`—` 语义**：`null` / 非有限数值一律 `—`；`data-dp4="null"` 明确标记原值为 null。

## A-53 多候选切换

- 切换只改 `selectedPos` 并重渲染已加载报告：**不重新请求报告、不重算、不修改 report**。
- `selectToken` 每次切换递增；A→B→A 时旧 A/旧 B 的迟到报告/几何响应不会覆盖当前 A。
- 切换前记录 `activeTab` 与 `.acp-nmr-scroll.scrollTop`，重渲染后恢复；切回仍保持。
- 切换会清除上一个候选的归属高亮（避免串台）；候选数据本身保持只读。
- 几何加载仅在“结构”tab 打开时发生，且每个选中候选独立守卫；报告本身不依赖几何。

## i18n keys（`next.nmr.*`）

本模块内联 zh 默认值；Shell 字典（`frontend/js/shell/i18n.js`）由 Shell 车道补
zh-CN/en-US 两份（本任务不改该文件）。当前 key 组：

```
next.nmr.title  subtitle  loading  error  retry  refresh  empty
next.nmr.badge.dp  badge.readonly  badge.warnings
next.nmr.no_transport  forbidden  invalid_report  read_failed
next.nmr.no_job  no_job_hint
next.nmr.not_nmr  not_nmr_hint
next.nmr.no_report  no_report_hint
next.nmr.no_candidates  no_candidates_hint
next.nmr.no_dp4  no_dp4_hint
next.nmr.no_spectrum  no_spectrum_hint
next.nmr.legacy_note  unknown_schema  placeholder_note
next.nmr.summary.title  winner_title  winner_none  winner_tag  not_rankable
next.nmr.label.{job,report,candidates,winner,winner_dp4,winner_dp5,nuclei,ranking,
  error_model,dp5_mode,protocol,status,dp4,dp5,probability,calibration,mode,model,
  reasons,analysis_status,conformers,coverage,assignment_count,expected,matched,
  selected,successful,population,flags,nucleus,element,slope,intercept,
  r2_regression,r2_prediction,mae,observation,claim_atoms,signal_group,conflict,
  atom,exp_ppm,calc_ppm,scaled_ppm,residual,reference_method,reference_ppm,
  formal_usable,n_claims,candidate_indices,diagnostics}
next.nmr.tab.{assignment,peaks,metrics,structure}
next.nmr.assignment.{title,hint,empty,clear,none_selected,highlight_atom,
  highlight_observation,highlight_related,highlight_none}
next.nmr.peaks.{title,hint,empty,summary,current_none}
next.nmr.metrics.{title,probability,coverage,evidence,regression,ensemble,
  processing,diagnostics,no_data,no_regression,no_processing,no_diagnostics,
  no_probability}
next.nmr.structure.{title,hint,loading,unavailable,error,ready,missing_labels,load,reload}
next.nmr.warn.{title,legacy_schema,unknown_schema,missing_summary,missing_nuclei,
  missing_processing,missing_diagnostics,missing_assignments,missing_regression,
  missing_coverage,missing_probability,dp4_unavailable,dp5_unavailable,
  not_rankable,analysis_status,placeholder_dp5,geometry_missing_labels}
next.nmr.status.{valid,invalid,evidence_insufficient,unavailable,not_applicable,
  placeholder,ok,degraded,unknown}
next.nmr.ranking.{normal,not_applicable}
next.nmr.errorTitle  errorMessage
```

说明：`status.*` / `ranking.*` / `tab.*` 为动态拼接族；`warn.*` 中未实际发出的
`geometry_missing_labels` 与仅作说明的 `placeholder_note`/`winner_title`/`title` 组可随
A-61 全键整理时一并裁剪，不影响当前行为。

## 接入步骤（需要 Shell / 装配车道配合，本任务文件范围外）

1. **`frontend/next/index.html`**：在模块脚本区加
   `<script src="/js/modules/nmr-report/module.js"></script>`；`<head>` 加
   `<link rel="stylesheet" href="/css/modules/nmr-report.css">`。
2. **布局/recipe**：在 `frontend/js/shell/layout-preferences.js` 的 `EXTRA_MODULES` 增加
   `nmr-report`，或在 `recipes.js` 的 Molecular/谱图 recipe 中登记
   `{ instanceId: "nmr-report", moduleId: "nmr-report", slot: "...", titleKey: "shell.module.nmr_report" }`。
3. **Shell i18n**：把上面 `next.nmr.*` 组写入 `shell/i18n.js` 的 zh-CN/en-US；未写入时
   模块显示内联 zh 文案（en 回退中文），`applyI18n` 行为不变。
4. **数据通道**：Shell bootstrap 已初始化 `ACPClient.transport.request` 与
   `ACPClient.jobDetail`；模块自动回退使用。若宿主有专用读适配器，注入 `deps.request`
   （签名同 `transport.request`）即可，不要在本模块加第二套请求栈。
5. **任务焦点**：`ACPShell.context.setFocus({ taskRef: { jobId, attempt } })` 即可驱动；
   远程任务需 `jobDetail` 可用（读 `node_id`）或宿主显式注入 `deps.isRemote=true`。

## 测试

```bash
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_nmr_report
```

覆盖：注册/descriptor/生命周期；no_job / not_nmr / no_report / no_candidates / no_dp4 /
no_spectrum 空态；loading；ready 摘要/候选/报告路径；三路径探测顺序；远程 preview 解包；
500 + 重试；无通道；非法 JSON；null→`—` 且不出现 0.000；server winner 唯一权威
（不本地排名）；归属值原样；归属行/信号组/观测峰/原子 chip 高亮；候选切换不重取/不修改
report；tab+scroll 保持；切换清高亮；A→B 迟到报告；A→B→A 迟到几何；dispose；
只读静态门（无 fetch/XHR/innerHTML/compute_dp4）；历史报告提示；i18n 命名空间/zh 回退。

**结果：30 passed / 0 failed**（2026-10-31，Node v24.21.0，`run_all.js` 非空门通过）。
全量 `run_all.js`：**906 passed / 0 failed**（含本模块 30 项；本机未跑浏览器套件）。

## 已知缺口 / 不确定项（未通过不写成通过）

- **没有专用 NMR 报告/谱图端点**：`v1_science.py`/`v2_science.py` 无 NMR 路由；本模块按
  BE-GAP-04 使用既有 job-files 端点。若后续要渲染真正的谱图曲线（强度 vs ppm），
  `nmr_report.json` 不带谱图序列，需要新的后端产品/契约；当前只显示观测峰认领表。
- **没有服务端“归属高亮”字段**：高亮是 UI 关联状态（同观测/同信号组），不是报告内容；
  报告本身不被修改。
- **远程判定依赖可选 `jobDetail`**：Shell `taskRef` 只有 `{jobId, attempt}`，没有 remote 标志。
  若 `ACPClient.jobDetail` 不可用且宿主未注入 `deps.isRemote`，远程任务会按本地探测并
  得到 `no_report`（不会伪造数据）。接入时需保证 `jobDetail` 或显式 remote。
- **报告是任务级而非 attempt 级**：文件端点解析当前 `work_dir`；未把 `taskRef.attempt`
  拼进路径（服务端也没有 attempt 级 NMR 报告路由）。
- **结构 tab 不是 3D 查看器**：只显示原子数/元素组成/原子 chip，用于归属标签校验；
  3D 渲染属于 A-33 `molecule-viewer`，本模块不重复建 3D 栈。
- **单文件无分页/虚拟化**：大报告候选/归属行全量渲染；后续可加窗口化。
- **未改** `index.html` / `layout-preferences.js` / `recipes.js` / `shell/i18n.js`（文件所有权
  限制），因此真实 `/next/` 页面尚未加载本模块；浏览器 click-through 未执行，不记为 PASS。
- **未跑真实服务/真实 nmr 任务**：测试为 fixture/vm 层；真实报告读取只以既有 Python 测试
  `tests/test_acp_api_v1.py::test_v1_job_files_serves_nmr_report_new_layout` 的契约为依据。
