ACP Molecular Workbench -- Frontend Redesign Plan (v2, reviewed)
==================================================================

**Date:** 2026-06-27 (revised after code review)
**Status:** Design specification, ready for implementation
**File:** Replaces `frontend/ACP_Workbench_v2.html` (single HTML file, vanilla JS)
**Constraint:** NO npm/React/Vite. Single-file HTML/CSS/JS + 3Dmol.js CDN.

---

## 0. Review Corrections

This revision addresses all reviewer findings:

| Reviewer Issue | Resolution |
|----------------|------------|
| API path inconsistency (/api vs /api/v1) | Both are live. Use `/api/v1` as the single prefix via a `const API_BASE` constant. See Section 3. |
| Molecule resolve/embed "don't exist" | They DO exist: `POST /api/v1/molecule/resolve` and `POST /api/v1/molecule/embed` (Sprint 5, `v1_routes.py:531,551`). Verified by route scan (25 endpoints on `/api/v1`). |
| Project concept "no backend" | ProjectManager exists (Sprint 1, `projects.py`). `GET /api/v1/projects`, `POST /api/v1/projects`, etc. are all live (6 project routes on `/api/v1`). |
| `loadLatestStructure()` too fragile | Demoted to P1. P0 uses manual file-tree-click loading only. |
| Encoding risk | All strings ASCII-only in source. Chinese text stored in JS string literals (UTF-8 safe in `.html` file). |
| P0 scope bloat | P0 tightened to "workbench layout + file-tree-click + queue + modal". SMILES resolve, Ketcher, energy diagrams, auto-structure-load all moved to P1/P2. |

### Verified API inventory (both prefixes live)

**`/api/v1` -- 25 endpoints (USE THIS):**

| Category | Endpoints |
|----------|-----------|
| Status | `GET /status`, `GET /backends`, `GET /workflows`, `GET /protocols` |
| Projects | `GET /projects`, `POST /projects`, `GET/PATCH/DELETE /projects/{id}`, `GET /projects/{id}/jobs` |
| Jobs | `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel` |
| Events/Logs | `GET /jobs/{id}/events` (SSE), `GET /jobs/{id}/logs` |
| Files | `GET /jobs/{id}/files`, `GET /jobs/{id}/files/{path}` |
| Tasks | `GET /jobs/{id}/tasks`, `GET /tasks/{id}` |
| Artifacts | `GET /jobs/{id}/artifacts`, `GET /artifacts/{id}`, `GET /artifacts/{id}/download` |
| Molecule | `POST /molecule/resolve`, `POST /molecule/embed` |

**`/api` -- 12 endpoints (legacy, also live, do not use in new code):**
Status, backends, workflows, protocols, jobs CRUD, events, logs, files.

---

## 1. Core Philosophy Shift

| Aspect | Old (v2 current) | New (Workbench) |
|--------|-------------------|-----------------|
| **Main subject** | Job Queue table | 3D Molecule Canvas |
| **Job submission** | Left sidebar form (always visible) | Modal wizard (toolbar button) |
| **Job queue** | Full table in center | Collapsible panel in bottom-right |
| **File navigation** | None | Job file tree in left-top |
| **Molecule info** | Scattered in job detail | Dedicated INFO panel in left-bottom |
| **3D Viewer** | 360px box in right sidebar | Full center stage (55-70% screen) |
| **Language** | English only | Chinese default, EN toggle |
| **Inspiration** | Generic web dashboard | Bruker TopSpin / scientific workstation |

---

## 2. Target Layout

```
+----------------------------------------------------------------------+
| ACP 分子计算工作台  项目: [Uncategorized v]   搜索...    中文|EN  [OK]|
| 服务器: 12/32 cores | 队列 3 | 运行 1 | 失败 0                       |
+------------------+----------------------------------+----------------+
|                  |                                  |  TOOL DOCK     |
| JOB FILES        |                                  |  + 新建计算    |
| ----------------  |    3D MOLECULE CANVAS            |  绘制结构      |
| input/           |    (3Dmol.js, 55-70% screen)     |  导入文件      |
| v reactant.xyz   |                                  |  DFT 参数      |
| conformer/       |    [三维结构][构象][路径][能量][波函数]|  构象集合  |
|   crest_*.xyz    |                                  |  NMR 计算      |
| dft/opt/         |    Default: empty or last-loaded |  波函数分析    |
| report/          |    structure                      |  导出报告      |
|                  |                                  |                |
+------------------+                                  +----------------+
| INFO             |                                  |  TASK QUEUE    |
| 当前任务: ethanol|                                  |  运行 1|排队 2 |
| 分子式: C2H6O   |                                  |  完成 18|失败 1|
| 方法: censo-zero |                                  |  [expand v]    |
| 能量: -155.17 Eh |                                  |                |
| 状态: 已完成     |                                  |  LIVE LOGS     |
| 构象: 2          |                                  |  [14:32] ...   |
+------------------+----------------------------------+----------------+
```

### Grid specification

```css
.workbench {
  display: grid;
  grid-template-columns: 240px 1fr 220px;
  grid-template-rows: 1fr;
  grid-template-areas: "left-col canvas right-col";
  height: calc(100vh - 64px);
}
```

Left column splits internally (file-tree top 60%, info bottom 40%). Right column splits internally (tool-dock top, queue+logs bottom, both scrollable).

### Mobile fallback (< 1024px)

Canvas-only with bottom tab bar: [文件] [信息] [队列].

---

## 3. API Integration

### Single constant, single prefix

```javascript
const API_BASE = "/api/v1";

async function api(path, opts) {
  const response = await fetch(API_BASE + path, opts || {});
  if (!response.ok) {
    let msg = "HTTP " + response.status;
    try {
      const body = await response.json();
      if (body && body.detail) msg = String(body.detail);
    } catch (e) {}
    throw new Error(msg);
  }
  const ct = response.headers.get("content-type") || "";
  return ct.includes("application/json") ? response.json() : response.text();
}
```

ALL calls use `api("/jobs/...")`, `api("/projects")`, etc. The prefix is in one place.

### P0 endpoint usage map

| Frontend component | API call |
|--------------------|----------|
| Top bar status | `GET /status` |
| Top bar project selector | `GET /projects` |
| Left-top file tree | `GET /jobs/{id}/files` |
| Left-top file click (.xyz) | `GET /jobs/{id}/files/{path}` -> load into 3Dmol |
| Left-top file click (.log) | `GET /jobs/{id}/files/{path}` -> load into log panel |
| Left-bottom INFO | `GET /jobs/{id}` -> spec, status, result |
| Center 3D viewer | 3Dmol.js renders XYZ fetched from file tree click |
| Right-bottom queue | `GET /jobs` |
| Right-bottom queue expand | `GET /jobs/{id}` for detail |
| Right-bottom logs | `GET /jobs/{id}/events` (SSE) or `GET /jobs/{id}/logs` |
| Job modal submit | `POST /jobs` |
| Job cancel | `POST /jobs/{id}/cancel` |

**No new API endpoints needed for P0.** All 12 calls above are existing `/api/v1` routes.

---

## 4. i18n System (Chinese Default)

### Approach

Vanilla JS dictionary with `t(key)` function. No external library.

```javascript
const I18N = {
  "zh-CN": {
    "app.title": "ACP 分子计算工作台",
    "app.subtitle": "分子计算与机理研究平台",
    "files.title": "任务文件",
    "info.title": "信息",
    "info.current_job": "当前任务",
    "info.formula": "分子式",
    "info.charge_spin": "电荷/自旋",
    "info.method": "计算方法",
    "info.energy": "最新能量",
    "info.status": "状态",
    "info.conformers": "构象数",
    "info.duration": "用时",
    "viewer.title": "三维结构",
    "viewer.empty": "点击左侧文件树中的 .xyz 文件以加载结构",
    "tab.3d": "三维结构",
    "tab.conformers": "构象集合",
    "tab.path": "反应路径",
    "tab.energy": "能量图",
    "tab.wavefunction": "波函数",
    "tool.new_calc": "新建计算",
    "tool.draw": "绘制结构",
    "tool.import": "导入文件",
    "tool.dft": "DFT 参数",
    "tool.conformers": "构象集合",
    "tool.nmr": "NMR",
    "tool.wavefunction": "波函数",
    "tool.export": "导出",
    "queue.title": "任务队列",
    "queue.running": "运行中",
    "queue.queued": "排队",
    "queue.completed": "完成",
    "queue.failed": "失败",
    "queue.expand": "展开",
    "queue.collapse": "收起",
    "queue.empty": "暂无任务",
    "log.title": "实时日志",
    "log.empty": "选择一个任务以查看日志",
    "modal.title": "新建计算任务",
    "modal.step1": "1. 输入结构",
    "modal.step2": "2. 选择工作流",
    "modal.step3": "3. 计算协议",
    "modal.step4": "4. 资源设置",
    "modal.smiles": "SMILES",
    "modal.file": "上传文件",
    "modal.workflow": "工作流",
    "modal.protocol": "协议",
    "modal.nproc": "核数",
    "modal.memory": "内存",
    "modal.job_name": "任务名称",
    "modal.cancel": "取消",
    "modal.submit": "提交",
    "status.online": "在线",
    "status.offline": "离线",
    "lang.toggle": "中文|EN",
  },
  "en-US": {
    "app.title": "ACP Molecular Workbench",
    "app.subtitle": "Molecular Computation & Mechanism Research",
    "files.title": "Job Files",
    "info.title": "Info",
    "info.current_job": "Current Job",
    "info.formula": "Formula",
    "info.charge_spin": "Charge/Spin",
    "info.method": "Method",
    "info.energy": "Last Energy",
    "info.status": "Status",
    "info.conformers": "Conformers",
    "info.duration": "Duration",
    "viewer.title": "3D Structure",
    "viewer.empty": "Click a .xyz file in the tree to load",
    "tab.3d": "3D Structure",
    "tab.conformers": "Conformers",
    "tab.path": "Reaction Path",
    "tab.energy": "Energy",
    "tab.wavefunction": "Wavefunction",
    "tool.new_calc": "New Calc",
    "tool.draw": "Draw",
    "tool.import": "Import",
    "tool.dft": "DFT",
    "tool.conformers": "Conformers",
    "tool.nmr": "NMR",
    "tool.wavefunction": "Wavefunction",
    "tool.export": "Export",
    "queue.title": "Task Queue",
    "queue.running": "Running",
    "queue.queued": "Queued",
    "queue.completed": "Done",
    "queue.failed": "Failed",
    "queue.expand": "Expand",
    "queue.collapse": "Collapse",
    "queue.empty": "No jobs",
    "log.title": "Live Logs",
    "log.empty": "Select a job to view logs",
    "modal.title": "New Calculation",
    "modal.step1": "1. Input Structure",
    "modal.step2": "2. Workflow",
    "modal.step3": "3. Protocol",
    "modal.step4": "4. Resources",
    "modal.smiles": "SMILES",
    "modal.file": "Upload File",
    "modal.workflow": "Workflow",
    "modal.protocol": "Protocol",
    "modal.nproc": "Cores",
    "modal.memory": "Memory",
    "modal.job_name": "Job Name",
    "modal.cancel": "Cancel",
    "modal.submit": "Submit",
    "status.online": "Online",
    "status.offline": "Offline",
    "lang.toggle": "中文|EN",
  },
};

let currentLang = "zh-CN";
function t(key) {
  return (I18N[currentLang] && I18N[currentLang][key]) || key;
}

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach(function(el) {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(function(el) {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-ph")));
  });
}
```

HTML elements use `data-i18n` for text content:
```html
<h1 data-i18n="app.title">ACP 分子计算工作台</h1>
```

---

## 5. Component Breakdown

### 5.1 Top Bar

```
+----------------------------------------------------------------------+
| [A] ACP 分子计算工作台    项目: [Uncategorized v]    中文|EN   [OK] |
|     分子计算与机理研究平台  服务器: 12 cores | 队列 3 | 运行 1       |
+----------------------------------------------------------------------+
```

- Brand mark + `data-i18n` title/subtitle
- Project selector: `<select>` populated from `GET /projects`
- Server summary: cores, queue counts from `GET /status`
- Language toggle: button that flips `currentLang` and calls `applyI18n()`
- Status pill

### 5.2 Left-Top: Job File Tree

**Data source:** `GET /jobs/{selectedJobId}/files` returns `{files: [{path, size, modified}]}`.

**Rendering:** Build a tree from flat paths and render as collapsible `<ul>`:

```javascript
function buildTree(paths) {
  const root = {};
  paths.forEach(function(p) {
    const parts = p.split("/");
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (!node[part]) node[part] = (i === parts.length - 1) ? {__file: true, __path: p} : {};
      node = node[part];
    }
  });
  return root;
}
```

**File click behavior (P0):**

| Extension | Action |
|-----------|--------|
| `.xyz` | Fetch content via `GET /jobs/{id}/files/{path}`, load into 3Dmol viewer |
| `.sdf` / `.mol` | Same as .xyz (3Dmol supports these formats) |
| `.log` / `.out` | Fetch content, display in log panel (right-bottom) |
| `.json` | Fetch and display as formatted text in log panel |
| other | Download link `<a href="/api/v1/jobs/{id}/files/{path}">` |

**File size guard:** Skip files > 10MB for inline loading (show "file too large" hint, keep download link).

### 5.3 Left-Bottom: INFO Panel

Populated when a job is selected:

```
INFO
---------------------------------
当前任务: ethanol_test
工作流:   conformer
协议:     censo-zero
分子式:   C2H6O (from SMILES CCO)
电荷:     0
自旋:     1
状态:     已完成
能量:     -155.1657 Eh
构象:     2
用时:     46s
```

Data extraction from `GET /jobs/{id}` response:
- `job.spec.name` -> task name
- `job.spec.workflow` -> workflow
- `job.spec.method.protocol` -> protocol
- `job.spec.input.source` -> SMILES (derive formula client-side or from log)
- `job.status` -> status
- `job.result.state.stages` -> parse final energy, conformer count
- `job.started_at` / `job.completed_at` -> duration

When no job selected: show placeholder text.

### 5.4 Center: 3D Molecule Canvas (Main Stage)

The 3D viewer occupies the full center area.

**Tab bar:**
```
[三维结构] [构象集合] [反应路径] [能量图] [波函数]
```

P0: Only "三维结构" tab is functional. Others show "coming soon" placeholder.

**3Dmol.js initialization:**

```javascript
let viewer = null;

function initViewer() {
  const container = document.getElementById("viewer-3d");
  viewer = $3Dmol.createViewer(container, {
    backgroundColor: "0x0e1419",
    antialias: true,
  });
  viewer.setViewStyle({ style: "outline" });
}

function loadXYZ(xyzContent) {
  if (!viewer) initViewer();
  viewer.removeAllModels();
  viewer.addModel(xyzContent, "xyz");
  viewer.setStyle({}, { stick: { radius: 0.15 }, sphere: { scale: 0.3 } });
  viewer.zoomTo();
  viewer.render();
}

window.addEventListener("resize", function() {
  if (viewer) viewer.resize();
});
```

**Viewer overlay toolbar:** [screenshot] [reset] [labels] (small buttons floating over top-right of canvas).

**Empty state:** Centered text: "点击左侧文件树中的 .xyz 文件以加载结构" (`data-i18n="viewer.empty"`).

### 5.5 Right-Top: Tool Dock

Vertical icon column. Each button triggers an action:

```
+----+
| +  |  data-i18n="tool.new_calc"  -> open job modal
+----+
| ✎  |  data-i18n="tool.draw"      -> P1 (SMILES input dialog)
+----+
| ↑  |  data-i18n="tool.import"     -> P1 (file upload)
+----+
| ⚙  |  data-i18n="tool.dft"        -> P1 (parameter drawer)
+----+
| 🧬 |  data-i18n="tool.conformers"  -> P1 (switch to conformer tab)
+----+
| 📈 |  data-i18n="tool.energy"      -> P1 (switch to energy tab)
+----+
| 🧪 |  data-i18n="tool.nmr"         -> P1 (NMR submission)
+----+
| 🌊 |  data-i18n="tool.wavefunction"-> P2 (wavefunction tab)
+----+
| 📄 |  data-i18n="tool.export"      -> P2 (export dialog)
+----+
```

P0: Only "+" (new calc) is functional. Others show tooltip "P1/P2 feature".

Icons: Inline SVG or Unicode characters. No icon library.

### 5.6 Right-Bottom: Task Queue + Logs

**Collapsed (default):**

```
任务队列                    [展开 v]
运行 1 | 排队 2 | 完成 18 | 失败 1
```

**Expanded:**

```
任务队列                                [收起 ^]
--------------------------------------------------------
名称              工作流     状态     进度    操作
ethanol_test      conformer  已完成   100%    [查看]
nmr_compound_18b  nmr        运行中   63%     [查看] [取消]
conformer_bench   conformer  排队     --      [取消]
--------------------------------------------------------
实时日志
[14:32:08] stage.started: crest_search
[14:32:25] stage.completed: crest_search
[14:32:26] stage.started: censo_part0
```

**Queue row click -> `selectJob(jobId)`:**
1. Fetch `GET /jobs/{id}` -> update INFO panel
2. Fetch `GET /jobs/{id}/files` -> update file tree
3. Connect SSE `GET /jobs/{id}/events` -> update log panel
4. (P1) Auto-load latest structure into 3D viewer

---

## 6. Job Submission Modal

**Trigger:** Click "+" in tool dock. Opens centered overlay.

```
+------------------------------------------+
| 新建计算任务                       [X]   |
+------------------------------------------+
|                                          |
| 1. 输入结构                               |
|   SMILES: [CCO                    ]      |
|   (任务名称: [ethanol_test       ])       |
|                                          |
| 2. 选择工作流                             |
|   [conformer v]                          |
|                                          |
| 3. 计算协议                               |
|   [censo-zero v]                         |
|                                          |
| 4. 资源设置                               |
|   核数: [4]   内存: [8GB]                |
|                                          |
|              [取消]  [提交]               |
+------------------------------------------+
```

P0: Only SMILES input (no file upload, no Ketcher, no "from current 3D").

**On submit:**
```javascript
async function submitJob() {
  const body = {
    workflow: modalWorkflow,
    name: modalName,
    input: { source: modalSmiles },
    method: { protocol: modalProtocol },
    resources: { nproc: modalNproc, mem: modalMem },
    project_id: selectedProjectId || null,
  };
  const result = await api("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  closeModal();
  refreshJobs();
  selectJob(result.job_id);
}
```

---

## 7. Linkage System

When user clicks a job in the queue:

```javascript
async function selectJob(jobId) {
  selectedJobId = jobId;

  // 1. Fetch job data
  const job = await api("/jobs/" + encodeURIComponent(jobId));
  const filesResp = await api("/jobs/" + encodeURIComponent(jobId) + "/files");

  // 2. Update INFO panel (left-bottom)
  updateInfoPanel(job);

  // 3. Update file tree (left-top)
  renderFileTree(filesResp.files, jobId);

  // 4. Update queue highlighting
  highlightQueueRow(jobId);

  // 5. Connect SSE log stream (right-bottom)
  openSSE(jobId);

  // P1: auto-load latest structure (deferred)
  // For P0: user manually clicks a .xyz file in the tree
}
```

---

## 8. Visual Design Tokens

```css
:root {
  --bg-base:       #0B0F14;
  --bg-panel:      #111821;
  --bg-elevated:   #1A2332;
  --bg-canvas:     #0E1419;

  --border-subtle: #1E2832;
  --border-default:#24303B;

  --text-primary:  #E5EDF5;
  --text-secondary:#B0BEC8;
  --text-tertiary: #6B7B8A;

  --accent:        #4EA1FF;
  --green:         #38C172;
  --amber:         #F2B84B;
  --red:           #E55353;

  --radius:        10px;
  --radius-sm:     6px;
  --transition:    0.15s ease;
}
```

Key principles:
1. 3D canvas is the visual center (largest, distinctive radial gradient background)
2. Side panels are subdued (lower contrast, smaller text)
3. Queue is compact (chips + mini progress, not full tables unless expanded)
4. Chinese font stack: `"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif`

---

## 9. Implementation Phases

### P0 -- Workbench Layout (must do, this round)

All P0 uses EXISTING `/api/v1` endpoints only. No new API.

| # | Task | Detail |
|---|------|--------|
| 1 | CSS grid restructure | 3-area workbench layout (left-col / canvas / right-col) |
| 2 | 3D viewer to center | Full-height 3Dmol.js viewer in center area |
| 3 | Job file tree (left-top) | Collapsible tree from `GET /jobs/{id}/files` |
| 4 | File click loading | `.xyz/.sdf/.mol` -> 3Dmol; `.log/.out` -> log panel |
| 5 | INFO panel (left-bottom) | Job metadata from `GET /jobs/{id}` |
| 6 | Tool dock (right-top) | Vertical icon column; only "+" active |
| 7 | Queue panel (right-bottom) | Collapsible; `GET /jobs`; status chips + mini table |
| 8 | Live log stream | SSE `GET /jobs/{id}/events` in queue panel |
| 9 | Job submission modal | SMILES -> workflow -> protocol -> resources -> `POST /jobs` |
| 10 | Chinese i18n | `I18N` dictionary, `data-i18n` attributes, `applyI18n()` |
| 11 | Language toggle | `中文|EN` button in top bar |
| 12 | Top bar update | Project selector + server summary + lang toggle |
| 13 | Remove old layout | Delete old left sidebar / center tabs / right sidebar from v2 |
| 14 | Job selection linkage | Click queue row -> update file tree + INFO + SSE |
| 15 | Center tab bar | 5 tabs; only "3D结构" active; others show placeholder |

### P1 -- Molecule Features (second round)

| # | Task | Prerequisite |
|---|------|-------------|
| 16 | SMILES resolve in modal | Uses existing `POST /molecule/resolve` |
| 17 | 3D embed preview in modal | Uses existing `POST /molecule/embed` |
| 18 | Auto-load latest structure | Smart file selection from job output |
| 19 | Conformer ensemble tab | Parse multi-frame XYZ, energy-sorted list |
| 20 | Log keyword highlighting | ERROR red, WARNING amber, SCF green |
| 21 | Project file tree API | New `GET /projects/{id}/files` endpoint |
| 22 | Ketcher integration | iframe for 2D drawing -> SMILES |
| 23 | 3D viewer toolbar | Screenshot, reset, measure, labels |

### P2 -- Advanced Visualization (future)

| # | Task |
|---|------|
| 24 | Energy diagram chart (SVG) |
| 25 | IRC/NEB trajectory animation |
| 26 | Cube isosurface (3Dmol.js `addIsosurface`) |
| 27 | NCI/RDG scatter plot |
| 28 | Mobile responsive (bottom tab bar) |
| 29 | Task templates (save/restore configs) |

---

## 10. Acceptance Criteria (P0)

- [ ] Page title and all section headers are Chinese by default
- [ ] `中文|EN` toggle switches all `data-i18n` text
- [ ] 3D viewer occupies center 55-70% of screen at 1280px+
- [ ] Job submission is via modal triggered by "+" tool dock button
- [ ] Left-top shows file tree for the selected job
- [ ] Clicking a `.xyz` file in the tree loads it into the 3D viewer
- [ ] Left-bottom shows INFO panel with job metadata
- [ ] Right-bottom shows collapsible queue + live logs
- [ ] Clicking a job in queue updates: file tree, INFO, logs (SSE)
- [ ] All API calls use `const API_BASE = "/api/v1"` prefix
- [ ] No new backend endpoints required
- [ ] Single HTML file, no build step
- [ ] Browser smoke test: load page, select job, click .xyz, see 3D structure, toggle language, submit fake job

---

## 11. File Structure

Single file `frontend/ACP_Workbench_v2.html`:

```
<style>   ~500 lines (design tokens, layout, components, responsive)
<body>    ~300 lines (semantic HTML structure with data-i18n)
<script>  ~700 lines (i18n, state, API, file tree, viewer, queue, modal, linkage)
Total:   ~1500 lines
```

This file REPLACES the current v2 HTML (not a new third file). The old `ACP_Workbench.html` remains at `/legacy/`.
